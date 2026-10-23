import importlib.util
import io
import json
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('comment_client',Path(__file__).resolve().parents[1]/'skills/socialquery-data/scripts/socialquery_client.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PRICING={'type':'comment_harvest','hackernewsCredits':2,'redditMaxCredits':100,'redditCreditsPerPage':1,'redditMinimumCredits':2,'facebookMaxCredits':100,'facebookCreditsPerPage':1,'facebookMinimumCredits':2,'tiktokMaxCredits':100,'tiktokCreditsPerPage':1,'tiktokMinimumCredits':2,'instagramCredits':5,'youtubeMaxCredits':100,'youtubeCreditsPerPage':1,'youtubeMinimumCredits':2,'maxSourceCalls':100}
ENDPOINT={'id':'get_prism_comments','path':'/v1/prism/comments','method':'GET','creditCost':2,'creditPricing':PRICING,'params':[{'name':'url','required':True}]}
class Opener:
    def __init__(self,endpoint=ENDPOINT): self.endpoint=endpoint;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps({'success':True,'credits_used':0,'data':{'endpoints':[self.endpoint]}}).encode())
class CommentHarvestBudgetTests(unittest.TestCase):
    def test_platform_holds_before_data_dispatch(self):
        for url,credits in [('https://www.reddit.com/r/AskReddit/comments/1lfbo7u/',100),('https://www.facebook.com/reel/753347914167361',100),('https://www.tiktok.com/@example/video/7517114944362499342',100),('https://youtube.com/watch?v=dQw4w9WgXcQ',100),('https://news.ycombinator.com/item?id=1',2),('https://www.instagram.com/p/BCDEFghiJk/',5)]:
            opener=Opener();client=module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(ENDPOINT['id'],{'url':url},credits-1)
            self.assertEqual(len(opener.requests),1)
            client.call_endpoint(ENDPOINT['id'],{'url':url},credits)
            self.assertEqual(opener.requests[-1].get_header('X-max-credits'),str(credits))
    def test_stale_tampered_and_unsupported_inputs(self):
        for pricing,url in [(None,'https://youtube.com/watch?v=dQw4w9WgXcQ'),({**PRICING,'youtubeMaxCredits':2},'https://youtube.com/watch?v=dQw4w9WgXcQ'),({**PRICING,'youtubeCreditsPerPage':True},'https://youtube.com/watch?v=dQw4w9WgXcQ'),(PRICING,'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ')]:
            opener=Opener({**ENDPOINT,'creditPricing':pricing});client=module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(ENDPOINT['id'],{'url':url},100)
            self.assertEqual(len(opener.requests),1)
