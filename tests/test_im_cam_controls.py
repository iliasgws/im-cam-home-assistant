"""Protocol, read-back and cancellation coverage for camera controls."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from custom_components.im_cam import ImCamCoordinator
from custom_components.im_cam.api import ControlSession, ImCamApi, ImCamError
from custom_components.im_cam.button import ImCamPtzButton
from custom_components.im_cam.switch import ImCamPrivacySwitch


class Socket:
    def __init__(self, responder=None):
        self.sent = []
        self.frames = asyncio.Queue()
        self.responder = responder
        self.closed = False

    async def send_json(self, message):
        self.sent.append(message)
        if self.responder and 'body' in message:
            body, ret = self.responder(message)
            frame = {'headers': {'req_id': message['headers']['req_id']}, 'ret': ret, 'body': body}
            await self.frames.put(SimpleNamespace(type=SimpleNamespace(name='TEXT'), data=json.dumps(frame)))

    async def receive(self):
        return await self.frames.get()

    async def close(self):
        self.closed = True


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_privacy_set_encoding_and_readback(self):
        ws = Socket(lambda msg: ([{'id':532,'value':'{"enable":true}'}] if msg['headers']['id']=='pub_dp_get' else {}, 0))
        state = await ControlSession(ws).set_privacy('CAMERA', True)
        self.assertTrue(state)
        write, read = ws.sent
        self.assertEqual(write['headers']['callee'], 'CAMERA')
        self.assertEqual(write['headers']['id'], 'pub_dp_set')
        self.assertEqual(write['body'][0]['id'], 532)
        self.assertEqual(json.loads(write['body'][0]['value']), {'enable': True})
        self.assertEqual(read['body']['dpids'], [532])

    async def test_privacy_missing_or_wrong_type_stays_unknown(self):
        for body in ([], [{'id':532,'value':'{"enable":"false"}'}]):
            ws = Socket(lambda msg: (body, 0))
            self.assertIsNone(await ControlSession(ws).read_privacy('CAMERA'))

    async def test_rejection_not_reported_as_success(self):
        ws = Socket(lambda msg: ({}, 160))
        with self.assertRaises(ImCamError):
            await ControlSession(ws).set_privacy('CAMERA', True)

    async def test_ptz_forward_sends_without_waiting_for_reply(self):
        ws = Socket()
        session = ControlSession(ws)
        for direction, code in [('up',1),('down',2),('left',3),('right',4),('stop',0)]:
            await session.ptz('CAMERA', direction)
            message = ws.sent[-1]
            self.assertEqual(message['headers']['callee'], 'CAMERA')
            self.assertEqual(message['body']['act'], 1)
            self.assertEqual(json.loads(message['body']['dp_list'][0]['value']), {'direction':code})

    async def test_request_ignores_unsolicited_reply(self):
        ws = Socket(lambda msg: ({'result':'correct'},0))
        await ws.frames.put(SimpleNamespace(type=SimpleNamespace(name='TEXT'),data='{"headers":{"req_id":"other"},"ret":0}'))
        reply = await ControlSession(ws).request('read', {})
        self.assertEqual(reply['body']['result'], 'correct')

    async def test_closed_socket_fails_immediately(self):
        ws = Socket()
        await ws.frames.put(SimpleNamespace(type=SimpleNamespace(name='CLOSED'),data=None))
        with self.assertRaises(ImCamError):
            await ControlSession(ws).request('read', {})

    async def test_regional_login_closes_both_sockets(self):
        sockets = [Socket(lambda msg: ({'switch_region':True,'url':['wss://region.invalid/fast']},0)),
                   Socket(lambda msg: ({'sessid':'example-session'},0))]
        endpoints = []
        class HTTP:
            async def ws_connect(self, endpoint, **kwargs):
                endpoints.append(endpoint)
                return sockets[len(endpoints)-1]
        api = ImCamApi(HTTP(), 'test@example.invalid', 'Example123')
        async with api.control_session() as session:
            self.assertEqual(session.session_id, 'example-session')
        self.assertTrue(all(socket.closed for socket in sockets))
        self.assertEqual(endpoints[-1], 'wss://region.invalid/fast')

    async def test_live_view_lease_is_reused_for_controls_until_last_user_exits(self):
        ws = Socket(lambda msg: ({'sessid':'example-session'},0))
        connections = []
        class HTTP:
            async def ws_connect(self, endpoint, **kwargs):
                connections.append(endpoint)
                return ws
        api = ImCamApi(HTTP(), 'test@example.invalid', 'Example123')
        async with api.control_session() as live:
            async with api.control_session() as control:
                self.assertIs(live, control)
            self.assertFalse(ws.closed)
            self.assertEqual(len(connections), 1)
        self.assertTrue(ws.closed)

    async def test_cancelled_ptz_stops_before_socket_closes(self):
        coordinator = object.__new__(ImCamCoordinator)
        coordinator._ptz_locks = {}
        moved = asyncio.Event()
        events = []
        class Session:
            async def ptz(self, serial, direction):
                events.append(direction)
                if direction != 'stop': moved.set()
        @asynccontextmanager
        async def context():
            try: yield Session()
            finally: events.append('closed')
        coordinator.async_control_session = context
        task = asyncio.create_task(coordinator.async_ptz('CAMERA', 'right'))
        await moved.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(events, ['right','stop','closed'])

    async def test_entities_share_device_and_disable_ptz_in_privacy(self):
        coordinator = SimpleNamespace(data={'CAMERA': {'alias':'Hall','online':True,'privacy':True}},last_update_success=True)
        switch = ImCamPrivacySwitch(coordinator,'CAMERA')
        up = ImCamPtzButton(coordinator,'CAMERA','up')
        stop = ImCamPtzButton(coordinator,'CAMERA','stop')
        self.assertEqual(switch.device_info['identifiers'], up.device_info['identifiers'])
        self.assertTrue(switch.is_on)
        self.assertFalse(up.available)
        self.assertTrue(stop.available)

    async def test_privacy_uses_readback_not_requested_state(self):
        coordinator = object.__new__(ImCamCoordinator)
        coordinator.data={'CAMERA':{'privacy':False}}
        coordinator.async_set_updated_data=MagicMock()
        class Session:
            async def set_privacy(self, serial, enabled): return False
        @asynccontextmanager
        async def context(): yield Session()
        coordinator.async_control_session=context
        with self.assertRaises(Exception): await coordinator.async_set_privacy('CAMERA',True)
        self.assertIs(coordinator.async_set_updated_data.call_args.args[0]['CAMERA']['privacy'], False)


if __name__ == '__main__': unittest.main()
