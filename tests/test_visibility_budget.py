import importlib.util
import io
import json
from pathlib import Path
import unittest
source = Path(__file__).resolve().parents[1] / 'skills/socialquery-data/scripts/socialquery_client.py'
spec = importlib.util.spec_from_file_location('visibility_client', source)
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)
PRICING = {'type': 'visibility_probes', 'creditsPerProbe': 2, 'baselineCredits': 5, 'defaultRuns': 8, 'maxRuns': 20, 'maxPrompts': 20, 'maxCredits': 1605, 'engines': ['perplexity', 'grok'], 'presets': {'quick': {'maxPrompts': 5, 'runs': 2}, 'standard': {'maxPrompts': 10, 'runs': 8}, 'deep': {'maxPrompts': 20, 'runs': 20}}}
ENDPOINT = {'id': 'get_prism_ai_visibility', 'path': '/v1/prism/ai-visibility', 'method': 'GET', 'creditCost': 2, 'creditPricing': PRICING, 'params': [{'name': name, 'required': name == 'brand'} for name in ['brand', 'topic', 'prompts', 'preset', 'runs', 'engines', 'include']]}
class Opener:
    def __init__(self, endpoint=ENDPOINT): self.endpoint = endpoint; self.requests = []
    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps({'success': True, 'credits_used': 0, 'data': {'endpoints': [self.endpoint]}}).encode())
class VisibilityBudgetTests(unittest.TestCase):
    def test_quotes_default_presets_single_engine_and_maximum(self):
        for params, credits in [({'brand':'A', 'topic':'Widgets'},32), ({'brand':'A', 'topic':'Widgets', 'preset':'quick'},8), ({'brand':'A', 'prompts':['First','Second'], 'preset':'standard', 'engines':'grok'},32), ({'brand':'A', 'prompts':['Q'+str(i) for i in range(20)], 'preset':'deep', 'include':'web_baseline'},1605)]:
            opener = Opener(); client = client_module.SocialQueryClient('synthetic', opener=opener)
            with self.assertRaises(client_module.SocialQueryError): client.call_endpoint(ENDPOINT['id'], params, credits-1)
            self.assertEqual(len(opener.requests),1)
            client.call_endpoint(ENDPOINT['id'], params, credits)
            self.assertEqual(opener.requests[-1].get_header('X-max-credits'),str(credits))
    def test_invalid_and_stale_metadata_or_presets_never_dispatch(self):
        for params in [{'brand':'A','topic':'Widgets','preset':'quick','runs':'3'}, {'brand':'A','prompts':['Q'+str(i) for i in range(6)],'preset':'quick'}, {'brand':'A','prompts':'A|Ａ'}, {'brand':'A','topic':'Widgets','runs':True}]:
            opener=Opener(); client=client_module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(client_module.SocialQueryError): client.call_endpoint(ENDPOINT['id'],params,1605)
            self.assertEqual(len(opener.requests),1)
        for pricing in [None,{**PRICING,'creditsPerProbe':1}]:
            opener=Opener({**ENDPOINT,'creditPricing':pricing}); client=client_module.SocialQueryClient('synthetic',opener=opener)
            with self.assertRaises(client_module.SocialQueryError): client.call_endpoint(ENDPOINT['id'],{'brand':'A','topic':'Widgets'},1605)
            self.assertEqual(len(opener.requests),1)
