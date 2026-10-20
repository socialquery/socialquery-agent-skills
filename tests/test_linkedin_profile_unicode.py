import importlib.util
import io
import json
from pathlib import Path
import unittest

source = Path(__file__).resolve().parents[1] / 'skills/socialquery-data/scripts/socialquery_client.py'
spec = importlib.util.spec_from_file_location('unicode_client', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ProfileUnicodeTests(unittest.TestCase):
    def test_unicode_identity_and_rejected_spellings(self):
        for value in ['josé-example', '你好-example', 'a\u0301', 'é' * 100, '𐐀' * 100, 'Case_Name-1']:
            encoded = module.urllib.parse.quote(value, safe='-_')
            expected = 'https://www.linkedin.com/in/' + encoded
            self.assertEqual(module._linkedin_profile_url('https://linkedin.com/in/' + value + '/'), expected)
            self.assertEqual(module._linkedin_profile_url(expected), expected)
        for value in ['', 'a'*101, '𐐀'*101, '%41', '%2F', '%C0%AF', '%E2%80%AEname', 'jos%c3%a9', 'josé-%C3%A9', 'name%3Fquery', 'name%00', 'name\u0085', '%F0%9F%98%80', '%25C3%25A9', 'a.b', '..', 'a b', 'a/b', 'a?b', '\ud800']:
            with self.subTest(value=value), self.assertRaises(module.SocialQueryError):
                module._linkedin_profile_url('https://linkedin.com/in/' + value)
        self.assertNotEqual(module._linkedin_profile_url('https://linkedin.com/in/é'), module._linkedin_profile_url('https://linkedin.com/in/e\u0301'))
        self.assertNotEqual(module._linkedin_profile_url('https://linkedin.com/in/Case'), module._linkedin_profile_url('https://linkedin.com/in/case'))

    def test_scoped_response_limits_include_envelope_and_remain_bounded(self):
        for path, maximum in [('/v1/linkedin/profile/all', 6*1024*1024+8192), ('/v1/linkedin/profile/complete', 4*1024*1024+8192), ('/v1/meta/endpoints', 4*1024*1024)]:
            prefix, suffix = b'{"success":true,"credits_used":0,"data":"', b'"}'
            raw = prefix + b'x' * (maximum-len(prefix)-len(suffix)) + suffix
            reads = []
            class Response(io.BytesIO):
                def read(self, size):
                    reads.append(size)
                    return super().read(size)
            class Opener:
                def open(self, request, timeout):
                    return Response(raw)
            client = module.SocialQueryClient('synthetic', opener=Opener())
            self.assertEqual(len(client.get(path)['data']), maximum-len(prefix)-len(suffix))
            raw += b' '
            with self.assertRaisesRegex(module.SocialQueryError, 'size limit'):
                client.get(path)
            self.assertEqual(reads, [maximum+1, maximum+1])

    def test_unicode_batch_quote_deduplicates_before_dispatch(self):
        endpoint = {'id':'profile', 'method':'GET', 'path':'/v1/linkedin/profile/all', 'creditCost':5, 'params':[{'name':'url','required':False},{'name':'urls','required':False}], 'creditPricing':{'type':'linkedin_profile_urls','maxItems':10,'creditsPerProfile':5}}
        data_calls = []
        class Opener:
            def open(self, request, timeout):
                if module.urllib.parse.urlparse(request.full_url).path == '/v1/meta/endpoints':
                    result = {'success':True,'credits_used':0,'data':{'endpoints':[endpoint]}}
                else:
                    data_calls.append(request)
                    result = {'success':True,'credits_used':5,'data':{}}
                return io.BytesIO(json.dumps(result).encode())
        client = module.SocialQueryClient('synthetic', opener=Opener())
        client.call_endpoint('profile', {'urls':'https://linkedin.com/in/josé/,https://www.linkedin.com/in/jos%C3%A9'}, 5)
        self.assertEqual(len(data_calls), 1)
        with self.assertRaises(module.SocialQueryError):
            client.call_endpoint('profile', {'url':'https://linkedin.com/in/jos%c3%a9'}, 5)
        self.assertEqual(len(data_calls), 1)
