import importlib.util,io,json,unittest
from pathlib import Path
source=Path(__file__).resolve().parents[1]/'skills/socialquery-data/scripts/socialquery_client.py'
spec=importlib.util.spec_from_file_location('callback_client',source)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class CallbackTests(unittest.TestCase):
    def test_strict_public_https_callback_shape(self):
        for url in ['https://hooks.example.com/events','https://example.com','https://hooks.example.com/v1?tenant=abc']:
            module._job_webhook({'url':url})
        for url in ['http://hooks.example.com/x','https://127.0.0.1/x','https://8.8.8.8/x','https://[::1]/x','https://a.local/x','https://a.internal/x','https://a.home.arpa/x','https://a.test/x','https://hooks.example.com:443/x','https://u:p@hooks.example.com/x','https://hooks.example.com/#','https://HOOKS.example.com/x','https://hooks.example.com./x','https://hooks.example.com/é','https://hooks.example.com/ bad','https://foo..example.com/x','https://hooks.example.com/a/../b','https://hooks.example.com/%2e%2e/b','https://hooks.example.com/%','https://hooks.example.com?'+('a'*(2048-len('https://hooks.example.com?')))]:
            with self.subTest(url=url),self.assertRaises(module.SocialQueryError):
                module._job_webhook({'url':url})
        for value in [None,[],{},'https://example.com',{'url':'https://example.com','secret':'caller'}]:
            with self.assertRaises(module.SocialQueryError):module._job_webhook(value)
    def test_callback_adds_no_customer_quote_or_automatic_request(self):
        endpoint={'id':'post_jobs','method':'POST','path':'/v1/prism/jobs','creditCost':0,'params':[{'name':'endpoint','required':True},{'name':'items','required':True},{'name':'webhook','required':False}],'creditPricing':{'type':'job_batch','maxItems':5000,'defaultCredits':1,'profileCredits':{'linkedin':5},'postCredits':{'instagram':2,'linkedin':5}}}
        calls=[]
        class Opener:
            def open(self,request,timeout):
                if module.urllib.parse.urlsplit(request.full_url).path=='/v1/meta/endpoints':result={'success':True,'credits_used':0,'data':{'endpoints':[endpoint]}}
                else:
                    calls.append(request)
                    result={'success':True,'credits_used':0,'data':{'job_id':'synthetic','webhook_secret':'synthetic-one-time-secret'}}
                return io.BytesIO(json.dumps(result).encode())
        client=module.SocialQueryClient('synthetic',opener=Opener())
        params={'endpoint':'prism/profiles','items':[{'platform':'bluesky','handle':'bsky.app'}],'webhook':{'url':'https://hooks.example.com/events'}}
        with self.assertRaises(module.SocialQueryError):client.call_endpoint('post_jobs',params,0,idempotency_key='synthetic-callback',allow_mutation=True)
        self.assertEqual(len(calls),0)
        self.assertEqual(client.call_endpoint('post_jobs',params,1,idempotency_key='synthetic-callback',allow_mutation=True)['credits_used'],0)
        self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(calls[0].data)['webhook'],params['webhook'])
        params['webhook']={'url':'https://a.local/x'}
        with self.assertRaises(module.SocialQueryError):client.call_endpoint('post_jobs',params,1,idempotency_key='synthetic-callback',allow_mutation=True)
        self.assertEqual(len(calls),1)
