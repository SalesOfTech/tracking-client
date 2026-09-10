import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core import signed_updates
from agent_tracker.core.files import atomic_json
from agent_tracker.core.release_manager import ReleaseManager


class UpdateCompatibilityTests(unittest.TestCase):
    def manifest(self, system='macos', minimum='13.0'):
        key = 'minimum_glibc' if system == 'linux' else 'minimum_os_version'
        return {'os': system, 'architecture':'x64', 'ui':'electron', key:minimum,
                'requires_compatibility_gate':True}

    def test_macos_floor_is_checked_numerically(self):
        for version,expected in [('10.15.7',False),('12.7.6',False),('13',True),('13.0',True),('13.6.1',True),('14.0',True),('',False),(None,False),('unknown',False)]:
            with self.subTest(version=version):
                self.assertEqual(signed_updates.runtime_compatible(self.manifest(),{'os':'macos','os_version':version}), expected)

    def test_windows_floor_and_qt_legacy(self):
        for version,expected in [('6.3.9600',False),('10.0.17763',False),('10.0.18362',True),('10.0.19045',True),('10.0.26100',True),('',False)]:
            with self.subTest(version=version):
                self.assertEqual(signed_updates.runtime_compatible(self.manifest('windows','10.0.18362'),{'os':'windows','os_version':version}),expected)
        legacy={'os':'windows','architecture':'x86','ui':'qt','target':'windows-x86-legacy'}
        self.assertTrue(signed_updates.runtime_compatible(legacy,{'os':'windows','os_version':'6.3.9600'}))

    def test_linux_requires_known_glibc_at_package_floor(self):
        for libc,version,expected in [('glibc','2.28',False),('glibc','2.35',True),('glibc','2.36',True),('musl','2.40',False),('','',False),('glibc','unknown',False)]:
            with self.subTest(libc=libc,version=version):
                self.assertEqual(signed_updates.runtime_compatible(self.manifest('linux','2.35'),{'os':'linux','libc':libc,'libc_version':version}),expected)

    def test_os_mismatch_is_not_compatible(self):
        self.assertFalse(signed_updates.runtime_compatible(self.manifest(),{'os':'windows','os_version':'20.0'}))

    def test_macos_detection_never_falls_back_to_darwin_kernel(self):
        with patch.object(signed_updates.platform,'system',return_value='Darwin'), patch.object(signed_updates.platform,'mac_ver',return_value=('',(),'')), patch.object(signed_updates.platform,'release',return_value='22.6.0') as kernel:
            self.assertFalse(signed_updates.runtime_compatible(self.manifest()))
            kernel.assert_not_called()

    def test_platform_detection_failure_stays_unknown(self):
        with patch.object(signed_updates.platform,'system',return_value='Darwin'), patch.object(signed_updates.platform,'mac_ver',side_effect=OSError('unavailable')):
            self.assertFalse(signed_updates.runtime_compatible(self.manifest()))

    def test_minimum_metadata_is_strict(self):
        for value in ('',None,True,13,{},'13.0-beta','-1','13\r\nInjected: 1','1.2.3.4.5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                signed_updates.validate_runtime_requirements(self.manifest(minimum=value))
        cases=[{'ui':'electron','os':'macos','architecture':'x64'},
               {'requires_compatibility_gate':True,'os':'macos'},
               dict(self.manifest(),architecture='x86'),
               dict(self.manifest(),requires_compatibility_gate='true'),
               dict(self.manifest(),minimum_glibc='2.35'),
               dict(self.manifest('linux','2.35'),minimum_os_version='22.04')]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                signed_updates.validate_runtime_requirements(value)

    def test_historical_manifests_do_not_gain_an_unverified_floor(self):
        with patch.object(signed_updates,'runtime_release_environment') as environment:
            self.assertTrue(signed_updates.runtime_compatible({'os':'macos','version':'3.1.0'}))
            environment.assert_not_called()

    def test_capability_headers_are_bounded_and_omit_unknown_values(self):
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos','os_version':'13.6.1'}):
            self.assertEqual(signed_updates.release_environment_headers(),{'X-Tracking-Update-Protocol':'1','X-Tracking-OS':'macos','X-Tracking-OS-Version':'13.6.1'})
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'linux','libc':'glibc','libc_version':'2.35'}):
            self.assertEqual(signed_updates.release_environment_headers()['X-Tracking-GLIBC'],'2.35')
        for libc,version in (('musl','2.40'),('glibc','unknown'),('','2.35')):
            with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'linux','libc':libc,'libc_version':version}):
                self.assertNotIn('X-Tracking-GLIBC',signed_updates.release_environment_headers())
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos','os_version':'bad\r\nheader'}):
            self.assertNotIn('X-Tracking-OS-Version',signed_updates.release_environment_headers())


class CatalogCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'install'
        self.key=SigningKey.generate()
        self.keys={'test':base64.b64encode(bytes(self.key.verify_key)).decode()}
        atomic_json(self.root/'trusted-update-keys.json',self.keys)
        atomic_json(self.root/'current.json',{'version':'3.1.0'})
        atomic_json(self.root/'versions/3.1.0/build.json',{'os':'macos','architecture':'x64','target':'macos-x64-modern'})
        self.manifest={'protocol':3,'os':'macos','architecture':'x64','target':'macos-x64-modern',
                       'version':'3.2.0','ui':'electron','minimum_os_version':'13.0',
                       'requires_compatibility_gate':True,'expires_at':int(time.time())+3600,
                       'url':'https://tracking.salesoftech.com/release.zip','size':123,'sha256':'a'*64}

    def signed(self,manifest=None):
        raw=json.dumps(self.manifest if manifest is None else manifest).encode()
        return {'key_id':'test','payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(self.key.sign(raw).signature).decode()}

    def manager(self):
        raw=json.dumps(self.signed()).encode()
        class Response:
            status_code=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_content(self,size):yield raw
        class Session:
            calls=[]
            def get(self,url,**kwargs):
                self.calls.append((url,kwargs))
                return Response()
        return ReleaseManager(self.root,Session())

    def test_mac12_check_declines_before_download_and_does_not_mark_failed(self):
        manager=self.manager()
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos','os_version':'12.7.6'}):
            self.assertIsNone(manager.check())
        self.assertEqual(len(manager.session.calls),1)
        self.assertTrue(manager.session.calls[0][0].endswith('/manifest.json'))
        self.assertEqual(manager.session.calls[0][1]['headers']['X-Tracking-OS-Version'],'12.7.6')
        self.assertEqual(manager.session.calls[0][1]['headers']['X-Tracking-Client-Version'],'3.1.0')
        self.assertEqual(manager.active()['version'],'3.1.0')
        for path in ('downloads','staging','pending.json','failed.json','stop-request.json'):
            self.assertFalse((self.root/path).exists())

    def test_new_session_has_server_gate_headers_without_losing_auth(self):
        import requests
        for environment,header,value in (
            ({'os':'macos','os_version':'13.6.1'},'X-Tracking-OS-Version','13.6.1'),
            ({'os':'windows','os_version':'10.0.19045'},'X-Tracking-OS-Version','10.0.19045'),
            ({'os':'linux','libc':'glibc','libc_version':'2.35'},'X-Tracking-GLIBC','2.35'),
        ):
            with self.subTest(environment=environment), requests.Session() as session:
                session.headers['Authorization']='Bearer fixture-only'
                with patch.object(signed_updates,'runtime_release_environment',return_value=environment):
                    manager=ReleaseManager(self.root,session)
                self.assertEqual(manager.session.headers[header],value)
                self.assertEqual(manager.session.headers['Authorization'],'Bearer fixture-only')
                with patch.object(signed_updates,'runtime_release_environment',return_value={'os':environment['os']}):
                    manager._environment_headers()
                self.assertNotIn('X-Tracking-OS-Version',manager.session.headers)
                self.assertNotIn('X-Tracking-GLIBC',manager.session.headers)

    def test_mac13_can_receive_verified_offer(self):
        manager=self.manager()
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos','os_version':'13.0'}):
            self.assertEqual(manager.check(),self.manifest)

    def test_unknown_os_version_declines_verified_offer(self):
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos'}):
            self.assertIsNone(self.manager().check())

    def test_direct_download_stage_and_installer_extraction_are_guarded(self):
        manager=self.manager()
        with patch.object(signed_updates,'runtime_release_environment',return_value={'os':'macos','os_version':'12.7'}):
            for operation in (lambda:manager.download(self.manifest),
                              lambda:manager.stage(self.root/'missing.zip',self.manifest),
                              lambda:signed_updates.stage_archive(self.root/'missing.zip',self.manifest,self.root/'extracted')):
                with self.assertRaisesRegex(ValueError,'current version retained'):
                    operation()
        self.assertEqual(manager.session.calls,[])
        self.assertFalse((self.root/'staging').exists())
        self.assertFalse((self.root/'extracted').exists())

    def test_minimum_fields_are_signature_protected(self):
        envelope=self.signed()
        envelope['payload']=base64.b64encode(json.dumps(dict(self.manifest,minimum_os_version='10.0')).encode()).decode()
        with self.assertRaises(BadSignatureError):
            signed_updates.verify_manifest(envelope,self.keys,'3.1.0','macos','x64')

    def test_missing_minimum_in_signed_electron_release_is_rejected(self):
        manifest=dict(self.manifest)
        del manifest['minimum_os_version']
        with self.assertRaisesRegex(ValueError,'explicit platform minimum'):
            signed_updates.verify_manifest(self.signed(manifest),self.keys,'3.1.0','macos','x64')


if __name__=='__main__':
    unittest.main()
