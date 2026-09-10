import base64
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.signed_updates import verify_manifest,stage_archive


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.private=SigningKey.generate()
        self.keys={'test':base64.b64encode(bytes(self.private.verify_key)).decode()}
        self.manifest={'protocol':3,'version':'3.0.1','os':'windows','architecture':'x64','expires_at':2000000000,'url':'https://example.test/update.zip','size':123,'sha256':'a'*64}

    def signed(self,manifest=None):
        raw=json.dumps(manifest or self.manifest).encode()
        return {'key_id':'test','payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(self.private.sign(raw).signature).decode()}

    def test_valid_release(self):
        self.assertEqual(self.manifest,verify_manifest(self.signed(),self.keys,'3.0.0','windows','x64',1900000000))

    def test_stable_upgrades_betas_but_cannot_downgrade_to_beta(self):
        stable = dict(self.manifest, version='3.0.0')
        for previous in ('3.0.0-beta.7', '3.0.0-beta.9'):
            self.assertEqual(stable, verify_manifest(self.signed(stable), self.keys, previous, 'windows', 'x64', 1900000000))
            with self.assertRaises(ValueError):
                verify_manifest(self.signed(dict(stable, version=previous)), self.keys, '3.0.0', 'windows', 'x64', 1900000000)

    def test_tamper_unknown_key_rollback_wrong_os_and_expiry(self):
        envelope=self.signed()
        envelope['payload']=base64.b64encode(b'{}').decode()
        with self.assertRaises(BadSignatureError): verify_manifest(envelope,self.keys,'3.0.0','windows','x64',1900000000)
        cases=[({},'3.0.0','windows','x64',1900000000),(self.keys,'3.0.1','windows','x64',1900000000),(self.keys,'3.0.0','linux','x64',1900000000),(self.keys,'3.0.0','windows','arm64',1900000000),(self.keys,'3.0.0','windows','x64',2100000000)]
        for args in cases:
            with self.assertRaises(ValueError): verify_manifest(self.signed(),*args)

    def test_archive_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,path in enumerate(['../outside','/outside','C:/outside','..\\outside','CON.txt','valid.txt']):
                archive=root/(str(index)+'.zip')
                with zipfile.ZipFile(archive,'w') as package: package.writestr(path,'test')
                raw=archive.read_bytes()
                info={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
                target=root/('stage'+str(index))
                if path=='valid.txt':
                    stage_archive(archive,info,target)
                    self.assertEqual('test',(target/'valid.txt').read_text())
                    with self.assertRaises(ValueError): stage_archive(archive,info,target)
                else:
                    with self.assertRaises(ValueError): stage_archive(archive,info,target)
                    self.assertFalse(target.exists())


if __name__ == '__main__': unittest.main()
