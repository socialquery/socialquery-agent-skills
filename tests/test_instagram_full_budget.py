import importlib.util
import io
import json
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('full_feed_client',Path(__file__).resolve().parents[1]/'skills/socialquery-data/scripts/socialquery_client.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PRICING={'type':'instagram_full_pages','singlePageCredits':5,'maxPages':10,'maxCredits':50,'maxLimit':50}
def endpoint(kind='posts',pricing=PRICING): return {'id':'full','path':'/v1/instagram/profile/'+kind+'/full','method':'GET','creditCost':5,'creditPricing':pricing,'params':[{'name':'handle','required':True},{'name':'limit'}]}
class Opener:
    def __init__(self,item): self.item=item;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps({'success':True,'credits_used':0,'data':{'endpoints':[self.item]}}).encode())
class InstagramFullBudgetTests(unittest.TestCase):
    def test_posts_and_reels_holds(self):
        for kind in ['posts','reels']:
            for params,credits in [({'handle':'sample'},5),({'handle':'sample','limit':1},50),({'handle':'sample','limit':'50'},50)]:
                opener=Opener(endpoint(kind));client=module.SocialQueryClient('synthetic',opener=opener)
                with self.assertRaises(module.SocialQueryError): client.call_endpoint('full',params,credits-1)
                self.assertEqual(len(opener.requests),1)
                client.call_endpoint('full',params,credits)
                self.assertEqual(opener.requests[-1].get_header('X-max-credits'),str(credits))
    def test_invalid_limits_and_stale_metadata_stop_before_data(self):
        for limit in [None,True,False,0,51,'1.0','1e1']:
            opener=Opener(endpoint());client=module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(module.SocialQueryError): client.call_endpoint('full',{'handle':'sample','limit':limit},50)
            self.assertEqual(len(opener.requests),1)
        for pricing in [None,{**PRICING,'maxCredits':5}]:
            opener=Opener(endpoint(pricing=pricing));client=module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(module.SocialQueryError): client.call_endpoint('full',{'handle':'sample','limit':1},50)
            self.assertEqual(len(opener.requests),1)
