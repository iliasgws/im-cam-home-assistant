"""Exercise entity construction and discovery against real HA entity classes."""
from types import SimpleNamespace
import unittest

from custom_components.im_cam import button, camera, select, switch
from custom_components.im_cam.config_flow import ImCamConfigFlow


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_cameras_get_all_controls_and_new_devices_added_once(self):
        listeners = []
        removed = []
        coordinator = SimpleNamespace(
            data={'ONE':{'alias':'First','online':True,'privacy':False},
                  'TWO':{'alias':'Second','online':None,'privacy':False}},
            last_update_success=True,
            async_add_listener=lambda listener: listeners.append(listener) or (lambda: None),
        )
        hass = SimpleNamespace(data={'im_cam':{'entry':coordinator}})
        entry = SimpleNamespace(entry_id='entry',async_on_unload=removed.append)
        entities = []
        for platform in (button,switch,camera,select):
            await platform.async_setup_entry(hass,entry,lambda additions: entities.extend(additions))
        self.assertEqual(len(entities),16)
        self.assertEqual(len({entity.unique_id for entity in entities}),16)
        cameras=[entity for entity in entities if isinstance(entity,camera.ImCamCamera)]
        self.assertEqual(len(cameras),2)
        self.assertTrue(all(entity._supports_native_async_webrtc for entity in cameras))
        self.assertTrue(all(entity.access_tokens for entity in cameras))
        self.assertEqual(len({next(iter(entity.device_info['identifiers'])) for entity in entities}),2)
        coordinator.data['THREE']={'alias':'Third','online':True,'privacy':False}
        for callback in listeners: callback()
        self.assertEqual(len(entities),24)
        for callback in listeners: callback()
        self.assertEqual(len(entities),24)
        self.assertEqual(len(removed),4)

    async def test_setup_still_requires_only_account_and_password(self):
        form=await ImCamConfigFlow().async_step_user()
        self.assertEqual({str(key) for key in form['data_schema'].schema},{'account','password'})


if __name__=='__main__': unittest.main()
