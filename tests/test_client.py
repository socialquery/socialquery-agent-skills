import importlib.util
import io
import json
from pathlib import Path
import unittest
import urllib.error

source = Path(__file__).resolve().parents[1] / "skills/socialquery-data/scripts/socialquery_client.py"
spec = importlib.util.spec_from_file_location("client", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ENDPOINT = {"id":"get_test", "method":"GET", "path":"/v1/test/search", "creditCost":1, "params":[{"name":"query", "required":True}]}

class Opener:
    def __init__(self, endpoint=ENDPOINT): self.requests=[]; self.endpoint=endpoint
    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps({"success":True,"credits_used":0,"data":{"endpoints":[self.endpoint]}}).encode())

class ClientTests(unittest.TestCase):
    def test_korea_global_query_is_literal_and_requires_social(self):
        endpoint = {**ENDPOINT, "path": "/v1/prism/korea-gap", "creditCost": 40, "creditPricing": {"type": "korea_gap", "defaultCredits": 40, "webOnlyCredits": 15}, "params": [{"name": "query", "required": True}, {"name": "global_query"}, {"name": "include"}]}
        opener = Opener(endpoint)
        client = module.SocialQueryClient("sq_test", opener=opener)
        client.call_endpoint(endpoint["id"], {"query": "브랜드", "global_query": "Public brand"}, 40)
        self.assertIn("global_query=Public+brand", opener.requests[-1].full_url)
        before = len([r for r in opener.requests if "/v1/prism/korea-gap?" in r.full_url])
        for patch in ({"global_query": ""}, {"global_query": "x\ny"}, {"global_query": "x"*501}, {"global_query": True}, {"global_query": "brand", "include": "quotes"}):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(endpoint["id"], {"query": "브랜드", **patch}, 40)
        self.assertEqual(len([r for r in opener.requests if "/v1/prism/korea-gap?" in r.full_url]), before)

    def test_connection_walk_page_quotes_and_stale_metadata(self):
        for path in ("/v1/instagram/followers", "/v1/instagram/following"):
            pricing = {"type": "per_item", "creditsPerItem": 5, "limitParameter": "max_pages", "defaultLimit": 1, "maxLimit": 5}
            item = {**ENDPOINT, "path": path, "creditCost": 5, "creditPricing": pricing, "params": [{"name": "handle"}, {"name": "coverage"}, {"name": "max_pages"}]}
            for pages in (1, 2, 5):
                opener = Opener(item)
                client = module.SocialQueryClient("sq_test", opener=opener)
                parameters = {"handle": "example", "coverage": "full", "max_pages": pages}
                with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"], parameters, pages*5-1)
                self.assertFalse(any(path in r.full_url for r in opener.requests))
                client.call_endpoint(item["id"], parameters, pages*5)
                self.assertEqual(opener.requests[-1].get_header("X-max-credits"), str(pages*5))
            for metadata in (None, {**pricing, "creditsPerItem": 1}, {**pricing, "maxLimit": True}):
                opener = Opener({**item, "creditPricing": metadata})
                with self.assertRaises(module.SocialQueryError): module.SocialQueryClient("sq_test", opener=opener).call_endpoint(item["id"], {"handle": "example", "coverage": "full", "max_pages": 2}, 25)
                self.assertFalse(any(path in r.full_url for r in opener.requests))

    def test_linkedin_discovery_profile_quotes_and_stale_metadata(self):
        for path in ("/v1/linkedin/post/reactions", "/v1/linkedin/post/reposts", "/v1/linkedin/search/people/by-url", "/v1/linkedin/search/people"):
            base = 5 if path.endswith("/reposts") else 10
            pricing = {"type": "profile_hydration", "baseCredits": base, "creditsPerProfile": 4, "defaultLimit": 10, "maxLimit": 10}
            item = {**ENDPOINT, "path": path, "creditCost": base, "creditPricing": pricing, "params": [{"name": "url", "required": True}, {"name": "include"}, {"name": "limit"}]}
            opener = Opener(item)
            client = module.SocialQueryClient("sq_test", opener=opener)
            for parameters, maximum in [({"url": "https://example.invalid"}, base), ({"url": "https://example.invalid", "include": "profile"}, base+40), ({"url": "https://example.invalid", "include": "profile", "limit": 1}, base+4)]:
                before = sum(path in r.full_url for r in opener.requests)
                with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"], parameters, maximum-1)
                self.assertEqual(sum(path in r.full_url for r in opener.requests), before)
                client.call_endpoint(item["id"], parameters, maximum)
                self.assertEqual(opener.requests[-1].get_header("X-max-credits"), str(maximum))
            for metadata in [None, {**pricing, "baseCredits": 0}, {**pricing, "creditsPerProfile": 1}, {**pricing, "maxLimit": True}]:
                invalid = Opener({**item, "creditPricing": metadata})
                with self.assertRaises(module.SocialQueryError): module.SocialQueryClient("sq_test", opener=invalid).call_endpoint(item["id"], {"url": "https://example.invalid", "include": "profile"}, 50)
                self.assertFalse(any(path in r.full_url for r in invalid.requests))

    def test_bing_snapshot_continuation_quotes_zero(self):
        item={**ENDPOINT,"path":"/v1/jobs/bing/search","creditCost":10,"params":[{"name":"query"},{"name":"location"},{"name":"cursor"}],"oneOfParameters":[["query","cursor"],["location","cursor"]]}
        opener=Opener(item); client=module.SocialQueryClient("sq_test",opener=opener)
        for params in [{"query":"engineer","location":"London"},{"cursor":"malformed"}]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,0)
        self.assertFalse(any(item["path"] in r.full_url for r in opener.requests))
        cursor="bsw1_"+"a"*120
        client.call_endpoint(item["id"],{"cursor":cursor},0)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"0")
        self.assertIn(cursor,opener.requests[-1].full_url)

    def test_linkedin_profile_csv_batch_holds(self):
        pricing={"type":"linkedin_profile_urls","maxItems":10,"creditsPerProfile":5}
        item={**ENDPOINT,"path":"/v1/linkedin/profile/all","creditPricing":pricing,"params":[{"name":"url"},{"name":"urls"}],"oneOfParameters":[["url","urls"]]}
        opener=Opener(item);client=module.SocialQueryClient("sq_test",opener=opener)
        a="https://www.linkedin.com/in/member-a";b="https://www.linkedin.com/in/member-b"
        for parameters,maximum in [({"url":a},5),({"urls":a+","+a+"/"},5),({"urls":a+","+b+",https://linkedin.com/in/member-a/"},10)]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],parameters,maximum-1)
            client.call_endpoint(item["id"],parameters,maximum)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"),str(maximum))
        before=sum("/v1/linkedin/profile/all" in r.full_url for r in opener.requests)
        for parameters in [{},{"url":a,"urls":b},{"urls":",".join([a]*11)},{"urls":a+","},{"urls":a+", "+b},{"urls":a+"?x=1"},{"urls":"https://user@linkedin.com/in/member"},{"urls":"https://linkedin.com/in/%2F"}]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],parameters,50)
        self.assertEqual(sum("/v1/linkedin/profile/all" in r.full_url for r in opener.requests),before)
        for patch in [{"maxItems":11},{"creditsPerProfile":1},{"maxItems":True}]:
            with self.assertRaises(module.SocialQueryError):module.SocialQueryClient("sq_test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{"url":a},50)

    def test_xiaohongshu_returned_row_holds(self):
        pricing={"type":"returned_rows","creditsPerItem":5,"limitParameter":"limit","defaultLimit":10,"maxLimit":20,"maxCredits":100}
        for path in ["/v1/xiaohongshu/search","/v1/xiaohongshu/profile/posts","/v1/xiaohongshu/trending"]:
            item={**ENDPOINT,"path":path,"creditPricing":pricing,"params":[{"name":"limit"}]}
            opener=Opener(item);client=module.SocialQueryClient("sq_test",opener=opener)
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{},49)
            self.assertFalse(any(path in r.full_url for r in opener.requests))
            for parameters,maximum in [({},50),({"limit":1},5),({"limit":20},100)]:
                client.call_endpoint(item["id"],parameters,maximum)
                self.assertEqual(opener.requests[-1].get_header("X-max-credits"),str(maximum))
            before=sum(path in r.full_url for r in opener.requests)
            for limit in [0,21,-1,1.5,True,None,"01","1e1"," 1","1\n"]:
                with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"limit":limit},100)
            self.assertEqual(sum(path in r.full_url for r in opener.requests),before)
            for patch in [{"creditsPerItem":1},{"defaultLimit":20},{"maxCredits":101},{"limitParameter":"rows"}]:
                with self.assertRaises(module.SocialQueryError):module.SocialQueryClient("sq_test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{},100)

    def test_douyin_returned_row_holds(self):
        pricing={"type":"returned_rows","creditsPerItem":5,"limitParameter":"limit","defaultLimit":10,"maxLimit":25,"maxCredits":125}
        for path in ["/v1/douyin/search","/v1/douyin/profile/posts","/v1/douyin/search/users"]:
            item={**ENDPOINT,"path":path,"creditPricing":pricing,"params":[{"name":"limit"}]}
            opener=Opener(item);client=module.SocialQueryClient("sq_test",opener=opener)
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{},49)
            self.assertFalse(any(path in r.full_url for r in opener.requests))
            for parameters,maximum in [({},50),({"limit":1},5),({"limit":25},125)]:
                client.call_endpoint(item["id"],parameters,maximum)
                self.assertEqual(opener.requests[-1].get_header("X-max-credits"),str(maximum))
            before=sum(path in r.full_url for r in opener.requests)
            for limit in [0,26,-1,1.5,True,None,"01","1e1"," 1","1\n"]:
                with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"limit":limit},125)
            self.assertEqual(sum(path in r.full_url for r in opener.requests),before)
            for patch in [{"creditsPerItem":1},{"defaultLimit":20},{"maxCredits":101},{"limitParameter":"rows"},{"maxLimit":20,"maxCredits":100}]:
                with self.assertRaises(module.SocialQueryError):module.SocialQueryClient("sq_test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{},125)

    def test_reddit_omni_hold(self):
        pricing={"type":"reddit_omni","baseCredits":1,"minimumCredits":5,"defaultThreads":8,"maxThreads":8,"maxCredits":9}
        item={**ENDPOINT,"path":"/v1/reddit/omni-search","creditPricing":pricing,"params":[{"name":n,"required":n=="query"} for n in ["query","threads","include"]]}
        opener=Opener(item);client=module.SocialQueryClient("sq_test",opener=opener)
        with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"query":"test"},8)
        self.assertFalse(any(item["path"] in r.full_url for r in opener.requests))
        for parameters,maximum in [({"query":"test"},9),({"query":"test","threads":"1"},5),({"query":"test","include":"subreddits"},5)]:
            client.call_endpoint(item["id"],parameters,maximum)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"),str(maximum))
        before=sum(item["path"] in r.full_url for r in opener.requests)
        for patch in [{"threads":True},{"threads":"01"},{"threads":9},{"include":"comments,comments"},{"include":""},{"query":"😀"*251}]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"query":"test",**patch},9)
        self.assertEqual(sum(item["path"] in r.full_url for r in opener.requests),before)
        for patch in [{"minimumCredits":0},{"defaultThreads":2},{"maxCredits":10},{"baseCredits":True}]:
            with self.assertRaises(module.SocialQueryError):module.SocialQueryClient("sq_test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{"query":"test"},9)

    def test_korea_gap_hold(self):
        pricing={"type":"korea_gap","defaultCredits":40,"webOnlyCredits":15}
        item={**ENDPOINT,"path":"/v1/prism/korea-gap","creditCost":40,"params":[{"name":"query","required":True},{"name":"include"}],"creditPricing":pricing}
        opener=Opener(item)
        client=module.SocialQueryClient("sq_test",opener=opener)
        with self.assertRaises(module.SocialQueryError):
            client.call_endpoint("get_test",{"query":"brand"},max_credits=39)
        client.call_endpoint("get_test",{"query":"brand","include":"quotes"},max_credits=15)
        client.call_endpoint("get_test",{"query":"brand"},max_credits=40)
        for include in ["other","social,social",True]:
            with self.assertRaises(module.SocialQueryError):
                client.call_endpoint("get_test",{"query":"brand","include":include},max_credits=40)

    def test_facebook_group_search_hold(self):
        pricing={"type":"group_search","baseCredits":1,"creditsPerGroup":1,"maxGroups":10,"maxCredits":11}
        item={**ENDPOINT,"path":"/v1/facebook/search/groups","creditPricing":pricing,"params":[{"name":n,"required":n=="query"} for n in ["query","include","page","cursor"]]}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        client.call_endpoint(item["id"],{"query":"Python"},1)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"1")
        before=sum(item["path"] in r.full_url for r in opener.requests)
        with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"query":"Python","include":"details"},10)
        self.assertEqual(sum(item["path"] in r.full_url for r in opener.requests),before)
        client.call_endpoint(item["id"],{"query":"Python","include":"details"},11)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"11")
        before=sum(item["path"] in r.full_url for r in opener.requests)
        for patch in [{"query":""},{"query":"site:facebook.com"},{"query":"x"*201},{"page":0},{"page":"01"},{"page":101},{"page":True},{"include":""},{"include":"details,details"},{"cursor":"foreign"},{"page":1,"cursor":"fbgs1_a.b"}]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{"query":"Python",**patch},11)
        self.assertEqual(sum(item["path"] in r.full_url for r in opener.requests),before)
        for patch in [{"baseCredits":0},{"creditsPerGroup":2},{"maxGroups":11},{"maxCredits":1}]:
            with self.assertRaises(module.SocialQueryError):module.SocialQueryClient(api_key="test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{"query":"Python"},11)

    def test_facebook_group_relevance_hold(self):
        pricing={"type":"group_search","baseCredits":1,"creditsPerGroup":1,"maxGroups":10,"maxCredits":12,"maxRelevanceCredits":1,"relevanceRowsPerCredit":25}
        item={**ENDPOINT,"path":"/v1/facebook/search/groups","creditPricing":pricing,"params":[{"name":n,"required":n=="query"} for n in ["query","include","page","cursor","relevance","relevance_threshold","relevant_to","judgments","label"]]}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        custom={"query":"Python","relevance":"filter","relevant_to":"Python programming","relevance_threshold":0.5}
        with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],custom,1)
        client.call_endpoint(item["id"],custom,2)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"2")
        with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{**custom,"include":"details"},11)
        client.call_endpoint(item["id"],{**custom,"include":"details"},12)
        before=sum(item["path"] in r.full_url for r in opener.requests)
        for patch in [{"relevance":"bad"},{"relevance_threshold":True},{"relevance_threshold":2},{"relevance_threshold":"1e-1"},{"relevance_threshold":"0.1234567"},{"relevant_to":""},{"relevant_to":False},{"relevant_to":"x\ny"},{"relevant_to":"x"*201}]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{**custom,**patch},12)
        self.assertEqual(sum(item["path"] in r.full_url for r in opener.requests),before)
        for patch in [{"judgments":"off"},{"label":"none"}]:client.call_endpoint(item["id"],{**custom,**patch},2)
        client.call_endpoint(item["id"],{"query":"Python","judgments":"off"},1)
        for patch in [{"maxRelevanceCredits":True},{"relevanceRowsPerCredit":10},{"maxCredits":11}]:
            with self.assertRaises(module.SocialQueryError):module.SocialQueryClient(api_key="test",opener=Opener({**item,"creditPricing":{**pricing,**patch}})).call_endpoint(item["id"],{"query":"Python"},12)

    def test_facebook_group_preview(self):
        pricing={"type":"group_search","baseCredits":1,"creditsPerGroup":1,"maxGroups":10,"maxCredits":12,"maxRelevanceCredits":1,"relevanceRowsPerCredit":25,"dryRunSupported":True}
        item={**ENDPOINT,"path":"/v1/facebook/search/groups","creditPricing":pricing,"params":[{"name":n,"required":n=="query"} for n in ["query","include","page","cursor","relevance","relevance_threshold","relevant_to","judgments","label","dry_run"]]}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        parameters={"query":"Python","dry_run":"1","include":"details","relevance":"filter","relevant_to":"Python programming"}
        client.call_endpoint(item["id"],parameters,0)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"0")
        before=sum(item["path"] in r.full_url for r in opener.requests)
        for invalid in [0,"0",True,False,"true","",2,"01",None]:
            with self.assertRaises(module.SocialQueryError):client.call_endpoint(item["id"],{**parameters,"dry_run":invalid},0)
        self.assertEqual(sum(item["path"] in r.full_url for r in opener.requests),before)
        for marker in [False,1]:
            with self.assertRaises(module.SocialQueryError):module.SocialQueryClient(api_key="test",opener=Opener({**item,"creditPricing":{**pricing,"dryRunSupported":marker}})).call_endpoint(item["id"],parameters,0)
        older={k:v for k,v in pricing.items() if k!="dryRunSupported"}
        with self.assertRaises(module.SocialQueryError):module.SocialQueryClient(api_key="test",opener=Opener({**item,"creditPricing":older})).call_endpoint(item["id"],parameters,0)

    def test_comment_leads_hold(self):
        pricing={"type":"comment_leads","defaultPosts":30,"maxPosts":90,"defaultOpen":12,"maxOpen":20,"defaultComments":50,"maxComments":200,"postsPerPage":30,"commentsPerPage":50,"labelsPerCredit":25,"maxCredits":243}
        item={**ENDPOINT,"path":"/v1/prism/comment-leads","creditCost":0,"params":[{"name":n,"required":n=="query"} for n in ["query","offer","posts","open","comments_per_post","platform","include"]],"creditPricing":pricing}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"query":"desk"},36)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],{"query":"desk"},37)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"37")
        client.call_endpoint(item["id"],{"query":"desk","posts":90,"open":20,"comments_per_post":200},243)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"243")
        for patch in [{"posts":0},{"posts":"01"},{"posts":91},{"open":True},{"comments_per_post":None},{"comments_per_post":201},{"query":""},{"offer":""},{"platform":"instagram"},{"include":"buyers"}]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"query":"desk",**patch},243)
        for patch in [{"maxCredits":244},{"postsPerPage":12},{"defaultOpen":20}]:
            bad={**item,"creditPricing":{**pricing,**patch}}
            with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test",opener=Opener(bad)).call_endpoint(item["id"],{"query":"desk"},243)


    def test_crisis_radar_conditional_hold(self):
        item={**ENDPOINT,"path":"/v1/prism/crisis-radar","creditCost":15,"params":[{"name":"brand","required":True},{"name":"confirm"}],"creditPricing":{"type":"boolean_option","parameter":"confirm","defaultCredits":15,"enabledCredits":45}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"brand":"Sample brand","confirm":True},44)
        self.assertEqual(len(opener.requests),1)
        for value in (True,"true"):
            client.call_endpoint(item["id"],{"brand":"Sample brand","confirm":value},45)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"45")
        for value in (False,"false"):
            client.call_endpoint(item["id"],{"brand":"Sample brand","confirm":value},15)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"15")
        client.call_endpoint(item["id"],{"brand":"Sample brand"},15)
        count=sum("/v1/prism/crisis-radar" in r.full_url for r in opener.requests)
        for value in (None,0,1,"yes",""):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"brand":"Sample brand","confirm":value},45)
        self.assertEqual(sum("/v1/prism/crisis-radar" in r.full_url for r in opener.requests),count)
        for patch in ({"parameter":"deep_scan"},{"defaultCredits":0},{"enabledCredits":15}):
            bad={**item,"creditPricing":{**item["creditPricing"],**patch}}
            with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test",opener=Opener(bad)).call_endpoint(item["id"],{"brand":"Sample brand","confirm":True},45)

    def test_investigation_budget(self):
        item={**ENDPOINT,"path":"/v1/prism/investigate","params":[{"name":"goal","required":True},{"name":"budget"},{"name":"max_steps"}],"creditPricing":{"type":"investigation_budget","defaultBudget":40,"maxBudget":200,"maxSteps":12}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"goal":"Find public product questions"}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,39)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,40)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"40")
        client.call_endpoint(item["id"],{**params,"budget":200,"max_steps":12},200)
        for patch in [{"budget":0},{"budget":201},{"budget":True},{"budget":None},{"budget":"01"},{"max_steps":13},{"goal":""}]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,**patch},200)
        bad={**item,"creditPricing":{**item["creditPricing"],"maxBudget":201}}
        with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test",opener=Opener(bad)).call_endpoint(item["id"],params,200)

    def test_news_quote(self):
        item={**ENDPOINT,"path":"/v1/search/news","creditCost":2,"params":[{"name":n} for n in ["query","engines","countries","depth","max_legs"]],"creditPricing":{"type":"news_legs","baseCredits":2,"googleCreditsPerLeg":1,"bingArticlesPerCredit":5,"maxLegs":12}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"query":"AI","engines":"google,bing","countries":"US,GB,us","depth":100,"max_legs":1}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,6)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,7)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"7")
        for patch in [{"depth":11},{"depth":True},{"engines":"web"},{"max_legs":0},{"countries":""}]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,**patch},62)

    def test_shop_market_budget(self):
        item={**ENDPOINT,"path":"/v1/tiktokshop/sales","creditCost":0,"params":[{"name":"regions"}],"creditPricing":{"type":"shop_regions","creditsPerRegion":1,"maxRegions":8,"defaultRegion":"US"}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"regions":"US,GB"},1)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],{"regions":"US,gb,US"},2)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"2")
        client.call_endpoint(item["id"],{},1)
        for regions in [0,None,"","US,","ZZ",",".join(["US"]*9)]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"regions":regions},8)
        bad=Opener({**item,"creditPricing":{**item["creditPricing"],"creditsPerRegion":True}})
        with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test",opener=bad).call_endpoint(item["id"],{},8)
        self.assertEqual(len(bad.requests),1)

    def test_comment_lookup_deep_scan_price(self):
        item={**ENDPOINT,"path":"/v1/tiktok/comment","creditCost":2,"params":[{"name":"deep_scan"}],"creditPricing":{"type":"boolean_option","parameter":"deep_scan","defaultCredits":2,"enabledCredits":6}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"deep_scan":True},5)
        self.assertEqual(len(opener.requests),1)
        for value in [True,"true"]:
            client.call_endpoint(item["id"],{"deep_scan":value},6)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"6")
        for value in [False,"false"]: client.call_endpoint(item["id"],{"deep_scan":value},2)
        client.call_endpoint(item["id"],{},2)
        for value in [0,1,None,"yes"]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"deep_scan":value},6)
        bad=Opener({**item,"creditPricing":{**item["creditPricing"],"enabledCredits":2}})
        with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test",opener=bad).call_endpoint(item["id"],{},6)
        self.assertEqual(len(bad.requests),1)

    def test_local_batch_pricing(self):
        item={**ENDPOINT,"path":"/v1/instagram/comment","creditCost":5,"params":[{"name":"deep_scan"}],"creditPricing":{"type":"boolean_option","parameter":"deep_scan","defaultCredits":5,"enabledCredits":15}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"deep_scan":True},14)
        client.call_endpoint(item["id"],{"deep_scan":True},15)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"15")
        creator={**ENDPOINT,"path":"/v1/prism/creator-card","creditCost":5,"params":[{"name":"platforms"}],"creditPricing":{"type":"creator_card","baseCredits":5,"includedPlatforms":4,"extraPlatformCredits":1,"maxPlatforms":7}}
        opener=Opener(creator);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"platforms":"tiktok,instagram,youtube,twitter,threads,bluesky,truthsocial,ignored,tiktok"}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(creator["id"],params,7)
        client.call_endpoint(creator["id"],params,8)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"8")
        batch={**ENDPOINT,"method":"POST","path":"/v1/prism/comment-lookup","params":[{"name":"items","required":True}],"creditPricing":{"type":"comment_lookup_batch","maxItems":25,"maxCredits":100}}
        opener=Opener(batch);client=module.SocialQueryClient(api_key="test",opener=opener)
        client.call_endpoint(batch["id"],{"items":[{"comment_url":"https://www.instagram.com/p/example/c/123456/"}]},5,idempotency_key="batch-test",allow_mutation=True)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"5")

    def test_handle_audit_platform_pricing(self):
        item = {**ENDPOINT, "path": "/v1/prism/handle-audit", "creditCost": 5, "params": [{"name": "handle", "required": True}, {"name": "platforms"}], "creditPricing": {"type": "creator_card", "baseCredits": 5, "includedPlatforms": 4, "extraPlatformCredits": 1, "maxPlatforms": 7}}
        opener = Opener(item)
        client = module.SocialQueryClient(api_key="test", opener=opener)
        params = {"handle": "sample", "platforms": "tiktok,instagram,youtube,twitter,threads,bluesky,truthsocial"}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"], params, 7)
        client.call_endpoint(item["id"], params, 8)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"), "8")
        count = len(opener.requests)
        for platforms in ("youtube,youtube", "reddit", "youtube, instagram"):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"], {"handle": "sample", "platforms": platforms}, 8)
        self.assertEqual(len(opener.requests), count + 3)
        stale = {k: v for k, v in item.items() if k != "creditPricing"}
        with self.assertRaises(module.SocialQueryError): module.SocialQueryClient(api_key="test", opener=Opener(stale)).call_endpoint(item["id"], {"handle": "sample"}, 8)

    def test_video_board_fixed_overhead_and_limit(self):
        item={**ENDPOINT,"path":"/v1/tiktok/videos/popular","creditCost":26,"params":[{"name":"limit"}],"creditPricing":{"type":"board_plus_item","baseCredits":25,"creditsPerItem":1,"defaultLimit":20,"maxLimit":20}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{},44)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],{},45)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"45")
        client.call_endpoint(item["id"],{"limit":4},29)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"29")
        for limit in [0,21,1.5,"2e1",True]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"limit":limit},45)

    def test_trend_board_fanout_budget(self):
        item={**ENDPOINT,"path":"/v1/tiktok/hashtags/popular","creditCost":6,"params":[{"name":"industry"}],"creditPricing":{"type":"trend_boards","creditsPerItem":2,"minimumCredits":6,"maxItems":48}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"industry":"all"},95)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],{"industry":"all"},96)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"96")
        client.call_endpoint(item["id"],{},6)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"6")

    def test_post_stat_batch_url_tiers(self):
        item={**ENDPOINT,"method":"POST","path":"/v1/prism/post-stats","params":[{"name":"urls","required":True}],"creditPricing":{"type":"post_stats_batch","maxItems":100,"defaultCredits":1,"platformCredits":{"instagram":2,"linkedin":5}}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"urls":["https://www.instagram.com/example/p/abc/","https://linkedin.com/posts/example","https://youtu.be/jNQXAC9IVRw","invalid"]}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,8,"underfunded",True)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,9,"stats-batch",True)
        self.assertEqual(json.loads(opener.requests[-1].data),params)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"9")

    def test_background_job_reservation(self):
        item={**ENDPOINT,"method":"POST","path":"/v1/prism/jobs","params":[{"name":"endpoint","required":True},{"name":"items","required":True},{"name":"include"},{"name":"since"}],"creditPricing":{"type":"job_batch","maxItems":5000,"defaultCredits":1,"profileCredits":{"linkedin":5},"postCredits":{"instagram":2,"linkedin":5}}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"endpoint":"prism/profiles","items":[{"platform":"instagram","handle":"sample"},{"platform":"linkedin","handle":"sample"}]}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,5,"underfunded",True)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,6,"batch-job-01",True)
        self.assertEqual(json.loads(opener.requests[-1].data),params)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"6")
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,"include":"comments"},100,"unsupported",True)

        enriched={**params,"include":"posts","since":"2026-10-01","items":[{**params["items"][0],"stop_at_id":"known"},params["items"][1]]}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],enriched,6,"underfunded-posts",True)
        client.call_endpoint(item["id"],enriched,7,"enriched-job",True)
        self.assertEqual(json.loads(opener.requests[-1].data),enriched)
        for since in ["2026-02-30","2026-01-01T24:00:00Z","2026-01-01T00:00:00+01:60"]:
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**enriched,"since":since},7,"invalid-date",True)

    def test_profile_batch_tiers_and_nested_items(self):
        item={**ENDPOINT,"method":"POST","path":"/v1/prism/profiles","params":[{"name":"items","required":True}],"creditPricing":{"type":"profile_batch","maxItems":50,"defaultCredits":1,"platformCredits":{"linkedin":5}}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"items":[{"platform":"bluesky","handle":"bsky.app","custom_id":"first"},{"platform":"linkedin","handle":"example"}]}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,5,"underfunded",True)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,6,"profile-batch",True)
        self.assertEqual(json.loads(opener.requests[-1].data),params)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"6")

    def test_similar_profile_hydration_pricing(self):
        item={**ENDPOINT,"path":"/v1/instagram/similar","creditCost":5,"params":[{"name":"handle"},{"name":"include"},{"name":"limit"}],"creditPricing":{"type":"profile_hydration","baseCredits":5,"creditsPerProfile":1,"defaultLimit":20,"maxLimit":80}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"handle":"alice","include":"profile"}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,24)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,25)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"25")
        client.call_endpoint(item["id"],{**params,"limit":80},85)
        client.call_endpoint(item["id"],{"handle":"alice","limit":80},5)
        for patch in ({"limit":None},{"limit":True},{"limit":0},{"limit":81},{"include":"other"}):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,**patch},85)

    def test_company_people_profile_hydration_pricing(self):
        item={**ENDPOINT,"path":"/v1/linkedin/company/people","creditCost":10,"params":[{"name":"company_id"},{"name":"include"},{"name":"limit"}],"creditPricing":{"type":"profile_hydration","baseCredits":10,"creditsPerProfile":4,"defaultLimit":10,"maxLimit":10}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"company_id":"1035","include":"profile","limit":3}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,21)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,22)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"22")
        client.call_endpoint(item["id"],{"company_id":"1035","include":"profile"},50)
        client.call_endpoint(item["id"],{"company_id":"1035"},10)
        for patch in ({"limit":None},{"limit":True},{"limit":0},{"limit":11},{"include":"other"}):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,**patch},50)

    def test_company_people_rejects_legacy_underquote(self):
        item={**ENDPOINT,"path":"/v1/linkedin/company/people","creditCost":10,"params":[{"name":"company_id"},{"name":"include"}]}
        item.pop("creditPricing",None)
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"company_id":"1035","include":"profile"},50)
        self.assertEqual(len(opener.requests),1)

    def test_org_radar_pricing(self):
        item={**ENDPOINT,"path":"/v1/prism/org-radar","creditCost":26,"params":[{"name":"org","required":True},{"name":"repos"},{"name":"include"}],"creditPricing":{"type":"org_repositories","baseCredits":1,"creditsPerRepo":5,"defaultRepos":5,"maxRepos":10}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"org":"example","repos":2}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,10)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,11)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"11")
        client.call_endpoint(item["id"],{**params,"include":"rollup"},1)
        for patch in ({"repos":None},{"repos":True},{"repos":0},{"repos":11},{"include":""},{"include":"rollup,rollup"},{"include":"other"}):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,**patch},51)

    def test_enriched_reel_page_pricing(self):
        item={**ENDPOINT,"path":"/v1/facebook/profile/reels/full","params":[{"name":"url","required":True},{"name":"limit"}],"creditPricing":{"type":"enriched_reel_pages","creditsPerPage":5,"pageSize":10,"maxLimit":50}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"url":"https://facebook.com/example","limit":21}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,14)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,15)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"15")
        for limit in (0,51,True,None,1.5,"01","1e1"):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{**params,"limit":limit},25)

    def test_batch_transcript_budget_counts_ids(self):
        item={**ENDPOINT,"method":"POST","path":"/v1/youtube/transcripts","params":[{"name":"ids","required":True}],"creditPricing":{"type":"per_successful_input","arrayParameter":"ids","creditsPerItem":3,"maxItems":100}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        params={"ids":["bjVIDXPP7Uk","00000000000"]}
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],params,5,"batch-underfunded",True)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],params,6,"batch-ok",True)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"6")
        self.assertEqual(json.loads(opener.requests[-1].data),params)
    def test_mutations_preserve_method_and_body_with_explicit_intent(self):
        for method in ("POST", "PATCH", "DELETE"):
            item={"id":"change_job","method":method,"path":"/v1/web/jobs/{job_id}","creditCost":0,"params":[{"name":"job_id","required":True},{"name":"urls"},{"name":"options"}]}
            opener=Opener(item)
            client=module.SocialQueryClient(api_key="test",opener=opener)
            params={"job_id":"job_123","urls":["https://example.com"],"options":{"fresh":True,"limit":2}}
            with self.assertRaisesRegex(module.SocialQueryError,"allow-mutation"):
                client.call_endpoint(item["id"],params,0)
            with self.assertRaisesRegex(module.SocialQueryError,"idempotency"):
                client.call_endpoint(item["id"],params,0,allow_mutation=True)
            self.assertEqual(len(opener.requests),2)
            client.call_endpoint(item["id"],params,0,"job-action-123",True)
            request=opener.requests[-1]
            self.assertEqual(request.method,method)
            self.assertEqual(request.full_url,"https://socialquery.dev/v1/web/jobs/job_123")
            self.assertEqual(json.loads(request.data),{"urls":["https://example.com"],"options":{"fresh":True,"limit":2}})
    def test_metered_budget_and_server_cap(self):
        item={**ENDPOINT,"params":[*ENDPOINT["params"],{"name":"limit"}],"creditPricing":{"type":"per_item","creditsPerItem":2,"limitParameter":"limit","defaultLimit":25,"maxLimit":100}}
        opener=Opener(item);client=module.SocialQueryClient(api_key="test",opener=opener)
        with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"query":"x","limit":10},19)
        self.assertEqual(len(opener.requests),1)
        client.call_endpoint(item["id"],{"query":"x","limit":"10"},20)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"20")
        for limit in (None,True,0,101,"2.5"):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"query":"x","limit":limit},200)
    def test_resolved_prices_preserve_server_cap(self):
        item={**ENDPOINT,"path":"/v1/prism/lookup","params":[{"name":"url","required":True}],"creditPricing":{"type":"resolved_endpoint","maxCredits":5}}
        opener=Opener(item); client=module.SocialQueryClient(api_key="test",opener=opener)
        client.call_endpoint(item["id"],{"url":"https://github.com/octocat/Hello-World"},1)
        self.assertEqual(opener.requests[-1].get_header("X-max-credits"),"1")
        for cap in (None,True,-1,1.5):
            with self.assertRaises(module.SocialQueryError): client.call_endpoint(item["id"],{"url":"https://example.com"},cap)
        bad=Opener({**item,"path":"/v1/test/search"})
        with self.assertRaisesRegex(module.SocialQueryError,"Invalid resolved"):
            module.SocialQueryClient(api_key="test",opener=bad).call_endpoint(item["id"],{"url":"https://example.com"},5)
        self.assertEqual(len(bad.requests),1)
    def test_invalid_usage_is_not_success(self):
        class InvalidUsage:
            def open(self,*args,**kwargs): return io.BytesIO(json.dumps({"success":True,"credits_used":3,"data":{}}).encode())
        with self.assertRaisesRegex(module.SocialQueryError,"credit usage"):
            module.SocialQueryClient(api_key="test",opener=InvalidUsage()).request("GET","/v1/test",{},max_credits=2)
    def test_nested_invalid_number(self):
        opener=Opener()
        with self.assertRaisesRegex(module.SocialQueryError,"finite JSON"):
            module.SocialQueryClient(api_key="test",opener=opener).get("/v1/test",{"options":{"value":float("inf")}})
        self.assertEqual(opener.requests,[])
    def test_request_encoding_and_credentials(self):
        opener=Opener()
        client=module.SocialQueryClient(api_key="test-only",opener=opener)
        client.call_endpoint("get_test",{"query":"a & b"},1)
        self.assertEqual(len(opener.requests),2)
        self.assertIsNone(opener.requests[0].get_header("X-api-key"))
        self.assertEqual(opener.requests[1].get_header("X-api-key"),"test-only")
        self.assertIn("query=a+%26+b",opener.requests[1].full_url)
        self.assertNotIn("test-only",opener.requests[1].full_url)
    def test_invalid_requests_do_not_spend(self):
        for params,budget in [({},1),({"query":"x"},0),({"query":"x","unknown":"y"},1),([],1)]:
            opener=Opener()
            with self.assertRaises(module.SocialQueryError):
                module.SocialQueryClient(api_key="test",opener=opener).call_endpoint("get_test",params,budget)
            self.assertEqual(len(opener.requests),1)
    def test_origins_and_missing_key(self):
        opener=Opener()
        client=module.SocialQueryClient(api_key="",opener=opener)
        for path in ["https://evil.example", "//evil.example", "/v1/../admin", "/v1/%2e%2e/admin", "/api/admin", "/v1/github/profile"]:
            with self.assertRaises(module.SocialQueryError): client.get(path)
        self.assertEqual(opener.requests,[])
    def test_redirect_handler_refuses_following(self):
        self.assertIsNone(module.NoRedirects().redirect_request(None,None,302,"redirect",{},"https://evil.example"))
    def test_http_error_is_bounded_without_private_body(self):
        class Failure:
            count=0
            def open(self,*args,**kwargs):
                self.count+=1
                raise urllib.error.HTTPError("https://socialquery.dev",401,"bad",{"x-request-id":"req_123"},io.BytesIO(b"secret payload"))
        opener=Failure()
        with self.assertRaisesRegex(module.SocialQueryError,"HTTP 401; request ID: req_123") as error:
            module.SocialQueryClient(api_key="test",opener=opener).get("/v1/github/profile")
        self.assertNotIn("secret",str(error.exception))
        self.assertEqual(opener.count,1)


class AppReviewPricingTests(unittest.TestCase):
    def endpoint(self, pricing=None):
        return {"id": "get_prism_app_reviews", "method": "GET", "path": "/v1/prism/app-reviews", "creditCost": 15,
                "creditPricing": pricing or {"type": "store_reviews", "singleStoreCredits": 10, "bothStoresCredits": 15},
                "params": [{"name": n, "required": False} for n in ["query", "stores", "google_play_id", "app_store_id"]],
                "oneOfParameters": [["query", "google_play_id", "app_store_id"]]}

    def test_single_and_two_store_holds(self):
        cases = [({"google_play_id": "com.spotify.music"}, 10), ({"app_store_id": "324684580"}, 10),
                 ({"query": "spotify"}, 15), ({"query": "spotify", "stores": "app_store"}, 10),
                 ({"query": "spotify", "stores": "google_play, app_store"}, 15),
                 ({"google_play_id": "com.spotify.music", "app_store_id": "324684580"}, 15)]
        for parameters, cost in cases:
            opener = Opener(self.endpoint()); client = module.SocialQueryClient("synthetic", opener=opener)
            with self.assertRaises(module.SocialQueryError):
                client.call_endpoint("get_prism_app_reviews", parameters, cost - 1)
            self.assertEqual(len(opener.requests), 1)
            client.call_endpoint("get_prism_app_reviews", parameters, cost)
            self.assertEqual(opener.requests[-1].get_header("X-max-credits"), str(cost))

    def test_invalid_selection_never_dispatches(self):
        for parameters in [{}, {"query": ""}, {"query": "x\n"}, {"query": "x" * 201}, {"query": None},
                           {"query": "x", "stores": True}, {"query": "x", "stores": ""}, {"query": "x", "stores": "other"},
                           {"query": "x", "stores": "app_store,app_store"}, {"stores": "app_store"},
                           {"app_store_id": 123}, {"google_play_id": ""}, {"app_store_id": "324684580", "stores": "google_play"}]:
            opener = Opener(self.endpoint()); client = module.SocialQueryClient("synthetic", opener=opener)
            with self.assertRaises(module.SocialQueryError):
                client.call_endpoint("get_prism_app_reviews", parameters, 15)
            self.assertEqual(len(opener.requests), 1)

    def test_malformed_pricing_metadata(self):
        for patch in [{"singleStoreCredits": 0}, {"singleStoreCredits": "10"}, {"bothStoresCredits": 10}, {"bothStoresCredits": True}]:
            pricing = {"type": "store_reviews", "singleStoreCredits": 10, "bothStoresCredits": 15, **patch}
            opener = Opener(self.endpoint(pricing)); client = module.SocialQueryClient("synthetic", opener=opener)
            with self.assertRaises(module.SocialQueryError):
                client.call_endpoint("get_prism_app_reviews", {"query": "spotify"}, 15)
            self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__": unittest.main()
