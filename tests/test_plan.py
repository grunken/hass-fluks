"""Offline plan scheduling reuses the native Mapping executor and HA services."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fluks.output_execution import RuntimeOutputExecutor
from custom_components.fluks.plan import LocalPlan, plan_decisions
from custom_components.fluks.runtime import FluksRuntimeWebSocket


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.fail = False
    async def async_load(self):
        return deepcopy(self.data)
    async def async_save(self, data):
        if self.fail:
            raise OSError('disk unavailable')
        self.data = deepcopy(data)


def wire(now, *, seconds=10, expiry=30):
    stamp = lambda seconds: (now + timedelta(seconds=seconds)).isoformat()
    return {'generatedAt': stamp(-1), 'expiresAt': stamp(expiry), 'decisions': [
        {'at': stamp(seconds), 'battery.power': {'deviceId': 'battery', 'mode': 'target', 'value': '-2300'}},
        {'at': stamp(seconds), 'electricVehicle.power': {'deviceId': 'ev', 'mode': 'target', 'value': '0'}},
        {'at': stamp(expiry), 'battery.power': {'deviceId': 'battery', 'mode': 'release', 'value': None}},
        {'at': stamp(expiry), 'electricVehicle.power': {'deviceId': 'ev', 'mode': 'release', 'value': None}},
    ]}


def context():
    devices = [{'id': 'internal-'+t, 'deviceId': identity, 'type': t} for t, identity in [('battery','battery'),('electricVehicle','ev'),('site','site')]]
    mappings = []
    for device in devices:
        for mode in ['charge','discharge','hold','release','balance','target']:
            mappings.append({'id':device['id']+mode,'deviceId': device['id'], 'concept':device['type']+'.power', 'direction':'output','mode':mode,'configuration':{'version':1,'actions':[
                {'type':'serviceCall','service':'test.execute','target':{'entityId':'number.'+device['deviceId']},'data':{'value':{'kind':'literal','value':-1} if mode=='release' else {'kind':'requestedValue'}}},
            ]}})
    return {'devices':devices,'mappings':mappings,'catalog':[{'type':d['type'],'concepts':[{'concept':d['type']+'.power','datatype':'number','usages':['control']}]} for d in devices]}


async def setup(hass, store=None, ownership=None):
    if not hass.is_running:
        await hass.async_start()
    api=MagicMock()
    api.list_devices=AsyncMock(side_effect=AssertionError('Core must not be called offline'))
    api.list_mappings=AsyncMock(side_effect=AssertionError('Core must not be called offline'))
    api.get_device_type_catalog=AsyncMock(side_effect=AssertionError('Core must not be called offline'))
    output=RuntimeOutputExecutor(hass,api,'site',ownership_store=ownership or MemoryStore(),sleep=AsyncMock())
    plan=LocalPlan(hass,'entry','site',output,store=store or MemoryStore())
    await plan.async_load()
    if not plan.data['cache']:
        plan.data['cache']=context()
    return plan


async def advance(hass, freezer, now):
    freezer.move_to(now)
    async_fire_time_changed(hass, now)
    await hass.async_block_till_done()


async def test_wire_reception_atomic_persistence_and_no_online_execution(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(call.data))
    plan=await setup(hass)
    runtime=FluksRuntimeWebSocket(MagicMock(),'key','http://example.test',plan=plan)
    runtime._set_status('connected')
    now=dt_util.utcnow();message=wire(now)
    await runtime._handle_message({'fluks.plan':message})
    assert plan.store.data['plan']==message
    await advance(hass,freezer,now+timedelta(seconds=15))
    assert calls==[]
    assert runtime.status['plan']==message
    bad=deepcopy(message);bad['decisions'][0]['at']='not a timestamp'
    await runtime._handle_message({'fluks.plan':bad})
    assert plan.store.data['plan']==message
    await runtime.async_stop()


async def test_simultaneous_decisions_run_once_then_null_release_at_expiry(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now)
    await plan.async_receive(message)
    await advance(hass,freezer,now+timedelta(seconds=10))
    assert calls==[{'value':-2300,'entity_id':'number.battery'},{'value':0,'entity_id':'number.ev'}]
    await plan.async_receive(message)  # Wire retransmission cannot replay a claim.
    await advance(hass,freezer,now+timedelta(seconds=11))
    assert len(calls)==2
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert calls[-2:]==[{'value':-1,'entity_id':'number.battery'},{'value':-1,'entity_id':'number.ev'}]
    assert not plan.data['active']
    assert plan.state=='expired'
    await plan.async_stop()


async def test_restart_skips_past_targets_but_releases_previously_active_controls(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    store=MemoryStore();first=await setup(hass,store);now=dt_util.utcnow()
    await first.async_receive(wire(now))
    await advance(hass,freezer,now+timedelta(seconds=10))
    await first.async_stop()
    freezer.move_to(now+timedelta(seconds=20))
    second=await setup(hass,store)
    await advance(hass,freezer,now+timedelta(seconds=21))
    assert len(calls)==2
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert [c['value'] for c in calls]==[-2300,0,-1,-1]
    await second.async_stop()


async def test_reconnect_during_first_service_stops_remaining_actions(hass, freezer):
    calls=[];plan=await setup(hass)
    def receive(call):
        calls.append(dict(call.data));plan.set_connected(True)
    hass.services.async_register('test','execute',receive)
    now=dt_util.utcnow();await plan.async_receive(wire(now))
    await advance(hass,freezer,now+timedelta(seconds=10))
    assert len(calls)==1
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert len(calls)==1
    await plan.async_stop()


async def test_failed_durable_claim_never_executes(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(call.data))
    plan=await setup(hass);now=dt_util.utcnow();await plan.async_receive(wire(now))
    plan.store.fail=True
    await advance(hass,freezer,now+timedelta(seconds=10))
    assert calls==[] and plan.data['claimed']==[]
    await plan.async_stop()


async def test_expiry_releases_even_when_core_omits_terminal_decisions(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now);message['decisions']=message['decisions'][:2]
    await plan.async_receive(message)
    await advance(hass,freezer,now+timedelta(seconds=10))
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert [c['value'] for c in calls]==[-2300,0,-1,-1]
    await plan.async_stop()


async def test_existing_http_responses_refresh_offline_cache_without_requests(hass):
    plan=await setup(hass)
    cache=context()
    for kind, path in [('devices','/sites/site/devices'),('mappings','/sites/site/mappings'),('catalog','/canonical/device-types')]:
        await plan.async_cache_response('GET',path,{'data':cache[kind]},None)
    changed=deepcopy(cache['mappings'][0]);changed['configuration']['actions'][0]['data']['value']={'kind':'literal','value':123}
    await plan.async_cache_response('PATCH','/sites/site/mappings/'+changed['id'],{'data':changed},None)
    assert next(m for m in plan.data['cache']['mappings'] if m['id']==changed['id'])==changed
    await plan.async_cache_response('DELETE','/sites/site/mappings/'+changed['id'],{},None)
    assert all(m['id']!=changed['id'] for m in plan.data['cache']['mappings'])
    await plan.async_stop()


async def test_restart_after_expiry_only_cleans_up_offline_controls(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    store=MemoryStore();plan=await setup(hass,store);now=dt_util.utcnow()
    await plan.async_receive(wire(now));await advance(hass,freezer,now+timedelta(seconds=10));await plan.async_stop()
    freezer.move_to(now+timedelta(seconds=60))
    restored=await setup(hass,store)
    await advance(hass,freezer,now+timedelta(seconds=61))
    assert [c['value'] for c in calls]==[-2300,0,-1,-1]
    await restored.async_stop()
    again=await setup(hass,store)
    await advance(hass,freezer,now+timedelta(seconds=62))
    assert len(calls)==4
    await again.async_stop()


async def test_newer_plan_wins_and_failed_save_keeps_complete_previous_plan(hass):
    plan=await setup(hass);now=dt_util.utcnow();first=wire(now)
    await plan.async_receive(first)
    newer=wire(now+timedelta(seconds=1));await plan.async_receive(newer)
    await plan.async_receive(first);assert plan.plan==newer
    plan.store.fail=True
    with pytest.raises(OSError):await plan.async_receive(wire(now+timedelta(seconds=2)))
    assert plan.plan==newer and plan.store.data['plan']==newer
    await plan.async_stop()


async def test_same_concept_for_two_devices_at_same_time_is_not_collapsed(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    plan=await setup(hass);cache=plan.data['cache'];cache['devices'].append({'id':'ev-two','deviceId':'ev-two','type':'electricVehicle'})
    mapping=deepcopy(next(m for m in cache['mappings'] if m['concept']=='electricVehicle.power' and m['mode']=='charge'))
    mapping['deviceId']='ev-two';mapping['configuration']['actions'][0]['target']['entityId']='number.ev_two';cache['mappings'].append(mapping)
    now=dt_util.utcnow();message=wire(now);message['decisions'][0]={'at':message['decisions'][0]['at'],'electricVehicle.power':{'deviceId':'ev-two','mode':'target','value':'42'}}
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=10))
    assert calls==[{'value':42,'entity_id':'number.ev_two'},{'value':0,'entity_id':'number.ev'}]
    await plan.async_stop()


async def test_site_balance_suppression_is_preserved_across_offline_restart(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    store=MemoryStore();ownership=MemoryStore();plan=await setup(hass,store,ownership);now=dt_util.utcnow();message=wire(now)
    message['decisions']=[message['decisions'][0],{'at':message['decisions'][0]['at'],'site.power':{'deviceId':'site','mode':'balance','value':'0'}},{'at':(now+timedelta(seconds=20)).isoformat(),'battery.power':{'deviceId':'battery','mode':'target','value':'1000'}},message['decisions'][2],{'at':message['expiresAt'],'site.power':{'deviceId':'site','mode':'release','value':None}}]
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=10))
    assert calls==[{'value':0,'entity_id':'number.site'}]
    await plan.async_stop();freezer.move_to(now+timedelta(seconds=15));restored=await setup(hass,store,ownership)
    await advance(hass,freezer,now+timedelta(seconds=20));assert len(calls)==1
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert calls[-2:]==[{'value':-1,'entity_id':'number.site'},{'value':-1,'entity_id':'number.battery'}]
    await restored.async_stop()


async def test_offline_service_failure_retries_three_times_without_replay(hass, freezer, caplog):
    calls=[]
    def fail(call):
        calls.append(dict(call.data));raise RuntimeError('delivery uncertain')
    hass.services.async_register('test','execute',fail)
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now)
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=10))
    assert [call['entity_id'] for call in calls]==['number.battery']*3+['number.ev']*3
    assert 'failed after 3 attempts' in caplog.text
    assert len(plan.store.data['claimed'])==2
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=11))
    assert len(calls)==6
    await plan.async_stop()
    restored=await setup(hass,plan.store)
    await advance(hass,freezer,now+timedelta(seconds=12))
    assert len(calls)==6
    await restored.async_stop()


@pytest.mark.parametrize('failed_calls,update_on,expected', [(0,1,1),(1,2,2),(2,3,3),(0,2,2),(0,3,3)])
async def test_offline_retries_stop_when_result_is_verified(hass, freezer, failed_calls, update_on, expected):
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now)
    message['decisions']=message['decisions'][:1]
    mapping=next(m for m in plan.data['cache']['mappings'] if m['concept']=='battery.power' and m['mode']=='discharge')
    mapping['configuration']['actions'][0]['service']='number.set_value'
    hass.states.async_set('number.battery','0')
    received=[]
    def execute(call):
        received.append(dict(call.data))
        assert len(plan.store.data['claimed'])==1
        if len(received)<=failed_calls:
            raise RuntimeError('temporary service failure')
        if len(received)==update_on:
            hass.states.async_set('number.battery',str(call.data['value']))
    hass.services.async_register('number','set_value',execute)
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=10))
    assert received==[{'value':-2300,'entity_id':'number.battery'}]*expected
    assert hass.states.get('number.battery').state=='-2300'
    await plan.async_stop()


@pytest.mark.parametrize('reconnect', [False,True])
async def test_interrupted_retry_claim_is_not_replayed_after_restart(hass, freezer, reconnect):
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now)
    calls=[]
    def fail(call):
        calls.append(dict(call.data));raise RuntimeError('temporary service failure')
    async def interrupt(_delay):
        if reconnect:
            plan.set_connected(True)
            await plan.async_core_authority()
        else:
            raise asyncio.CancelledError
    plan.executor._sleep=interrupt
    hass.services.async_register('test','execute',fail)
    await plan.async_receive(message);await advance(hass,freezer,now+timedelta(seconds=10))
    assert len(calls)==1 and len(plan.store.data['claimed'])==2
    await plan.async_stop()
    restored=await setup(hass,plan.store)
    await advance(hass,freezer,now+timedelta(seconds=11))
    assert len(calls)==1
    # Only the future release Decisions run after restart; each keeps its retry budget.
    await advance(hass,freezer,now+timedelta(seconds=30))
    assert [call['value'] for call in calls]==[-2300]+[-1]*6
    await restored.async_stop()


async def test_plan_may_include_past_slots_without_replaying_them(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    plan=await setup(hass);now=dt_util.utcnow();message=wire(now)
    past=deepcopy(message['decisions'][0]);past['at']=(now-timedelta(minutes=15)).isoformat();message['decisions'].insert(0,past)
    await plan.async_receive(message)
    assert plan.plan['decisions'][0]==past
    await advance(hass,freezer,now+timedelta(seconds=1));assert not calls
    await advance(hass,freezer,now+timedelta(seconds=10));assert len(calls)==2
    await plan.async_stop()


async def test_disconnect_after_expiry_releases_last_core_control_without_target_replay(hass, freezer):
    calls=[];hass.services.async_register('test','execute',lambda call:calls.append(dict(call.data)))
    plan=await setup(hass);now=dt_util.utcnow();plan.set_connected(True)
    await plan.async_receive(wire(now))
    await advance(hass,freezer,now+timedelta(seconds=31));assert not calls
    plan.set_connected(False)
    await advance(hass,freezer,now+timedelta(seconds=32))
    assert calls==[{'value':-1,'entity_id':'number.battery'},{'value':-1,'entity_id':'number.ev'}]
    await plan.async_stop()
    restored=await setup(hass,plan.store)
    await advance(hass,freezer,now+timedelta(seconds=33));assert len(calls)==2
    await restored.async_stop()


async def test_local_storage_failure_does_not_veto_core_authority(hass):
    plan=await setup(hass)
    plan.data['active']={'battery|battery.power':{'concept':'battery.power','deviceId':'battery','mode':'target'}}
    plan.store.fail=True;plan.set_connected(True)
    await plan.async_core_authority()
    assert plan._connected and not plan.data['active']
    await plan.async_stop()


async def test_unreadable_saved_plan_does_not_prevent_runtime_startup(hass):
    if not hass.is_running:await hass.async_start()
    store=MemoryStore();store.async_load=AsyncMock(side_effect=OSError('storage unavailable'))
    plan=LocalPlan(hass,'entry','site',MagicMock(),store=store)
    await plan.async_load()
    assert plan.plan is None
    plan.set_connected(True);await plan.async_core_authority()
    assert plan._connected
    await plan.async_stop()
