"""Standard-library SocialQuery client. No retries or automatic pagination."""
import datetime
import json
import math
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = "https://socialquery.dev"
MAX_BYTES = 4 * 1024 * 1024


class SocialQueryError(Exception):
    pass


def _linkedin_profile_url(value):
    if not isinstance(value, str) or len(value) > 1240 or re.search(r"[\x00-\x20\x7f]", value):
        raise SocialQueryError("Invalid LinkedIn profile URL.")
    match = re.fullmatch(r"https://(?:www\.)?linkedin\.com/in/([^/?#]+)/?", value)
    if not match or len(match[1]) > 1200:
        raise SocialQueryError("Invalid LinkedIn profile URL.")
    try:
        decoded = urllib.parse.unquote(match[1], encoding="utf-8", errors="strict")
        encoded = urllib.parse.quote(decoded, safe="-_")
    except (ValueError, UnicodeError):
        raise SocialQueryError("Invalid LinkedIn profile URL.") from None
    if len(decoded) > 100 or any(ch not in "_-" and unicodedata.category(ch)[0] not in "LMN" for ch in decoded) or match[1] not in (decoded, encoded):
        raise SocialQueryError("Invalid LinkedIn profile URL.")
    return "https://www.linkedin.com/in/" + encoded


def _job_webhook(value):
    if not isinstance(value, dict) or set(value) != {"url"}:
        raise SocialQueryError("webhook must contain only a public HTTPS url.")
    raw = value["url"]
    if not isinstance(raw, str) or len(raw) > 2048 or re.search(r"[^\x21-\x7e]", raw) or "#" in raw:
        raise SocialQueryError("Invalid public HTTPS webhook URL.")
    if re.search(r"[<>\"'`{}\\^|]", raw) or re.search(r"%(?![a-fA-F0-9]{2})", raw):
        raise SocialQueryError("Use canonical URL escapes.")
    match = re.match(r"https://([a-z0-9.-]+)(?:[/?]|$)", raw)
    try:
        url = urllib.parse.urlsplit(raw)
        if not match or match[1] != url.hostname or url.scheme != "https" or url.username or url.password or url.port or url.fragment:
            raise ValueError()
        if len(urllib.parse.urlunsplit((url.scheme, url.netloc, url.path or "/", url.query, ""))) > 2048 or any(part.lower() in (".","..","%2e",".%2e","%2e.","%2e%2e") for part in url.path.split("/")):
            raise ValueError()
        labels = url.hostname.split(".")
        if len(url.hostname) > 253 or len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels) or not re.fullmatch(r"[a-z]{2,63}", labels[-1]) or labels[-1] in ("localhost","local","internal","lan","home","corp","onion","invalid","test","arpa"):
            raise ValueError()
    except ValueError:
        raise SocialQueryError("Invalid public HTTPS webhook URL.") from None


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SocialQueryClient:
    def __init__(self, api_key=None, opener=None):
        self.api_key = os.environ.get("SOCIALQUERY_API_KEY") if api_key is None else api_key
        self.opener = opener or urllib.request.build_opener(NoRedirects())

    def get(self, path, parameters=None, authenticated=True, idempotency_key=None):
        return self.request("GET", path, parameters, authenticated, idempotency_key)

    def request(self, method, path, parameters=None, authenticated=True, idempotency_key=None, max_credits=None):
        if not isinstance(path, str) or not re.fullmatch(r"/v1/[A-Za-z0-9_/-]+", path) or "//" in path:
            raise SocialQueryError("Unsupported API path. Use an endpoint from the public catalog.")
        if method not in ("GET", "POST", "PATCH", "DELETE"):
            raise SocialQueryError("Unsupported HTTP method.")
        if authenticated and not self.api_key:
            raise SocialQueryError("Set SOCIALQUERY_API_KEY in your environment.")
        query = {}
        for name, value in (parameters or {}).items():
            try:
                encoded = value if isinstance(value, str) else json.dumps(value, allow_nan=False, separators=(",", ":"))
            except (ValueError, TypeError, RecursionError):
                raise SocialQueryError("Parameters must contain finite JSON values.") from None
            if len(encoded) > 16000:
                raise SocialQueryError("Parameter exceeds 16000 characters.")
            query[name] = encoded
        url = ORIGIN + path + ("?" + urllib.parse.urlencode(query) if query and method == "GET" else "")
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["x-api-key"] = self.api_key
        if idempotency_key is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key):
                raise SocialQueryError("Invalid idempotency key.")
            headers["Idempotency-Key"] = idempotency_key
        if max_credits is not None:
            if type(max_credits) is not int or max_credits < 0 or max_credits > 9007199254740991:
                raise SocialQueryError("Invalid credit cap.")
            headers["X-Max-Credits"] = str(max_credits)
        body = None
        if method != "GET":
            headers["Content-Type"] = "application/json"
            body = json.dumps(parameters or {}, allow_nan=False).encode()
        request = urllib.request.Request(url, headers=headers, method=method, data=body)
        maximum = 6 * 1024 * 1024 + 8192 if path == "/v1/linkedin/profile/all" else MAX_BYTES + 8192 if path == "/v1/linkedin/profile/complete" else MAX_BYTES
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read(maximum + 1)
        except urllib.error.HTTPError as error:
            request_id = re.sub(r"[^A-Za-z0-9_-]", "", error.headers.get("x-request-id", "unavailable"))[:100]
            error.close()
            raise SocialQueryError(f"SocialQuery HTTP {error.code}; request ID: {request_id}. No automatic retry was made.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SocialQueryError("SocialQuery request failed or timed out. No automatic retry was made.") from None
        if len(raw) > maximum:
            raise SocialQueryError("API response exceeds the endpoint size limit. Narrow the query or page size.")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SocialQueryError("API returned invalid JSON.") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            raise SocialQueryError("SocialQuery returned an unsuccessful response.")
        if max_credits is not None and (type(result.get("credits_used")) is not int or result["credits_used"] < 0 or result["credits_used"] > max_credits):
            raise SocialQueryError("Response credit usage exceeded the approved cap or was invalid.")
        return result

    def endpoints(self):
        result = self.get("/v1/meta/endpoints", authenticated=False)
        endpoints = result.get("data", {}).get("endpoints")
        if not isinstance(endpoints, list):
            raise SocialQueryError("Invalid endpoint catalog.")
        return endpoints

    def call_endpoint(self, endpoint_id, parameters, max_credits, idempotency_key=None, allow_mutation=False):
        endpoint = next((item for item in self.endpoints() if item["id"] == endpoint_id), None)
        if endpoint is None:
            raise SocialQueryError("Unknown endpoint ID. Inspect the public endpoint catalog.")
        if endpoint["method"] not in ("GET", "POST", "PATCH", "DELETE"):
            raise SocialQueryError("Unsupported endpoint method.")
        if endpoint["method"] != "GET" and not allow_mutation:
            raise SocialQueryError("Use --allow-mutation for a user-requested POST, PATCH, or DELETE action; the default is GET only.")
        if endpoint["method"] != "GET" and not idempotency_key:
            raise SocialQueryError("Mutating requests require an idempotency key for safe deliberate retries.")
        if not isinstance(parameters, dict):
            raise SocialQueryError("Parameters must be an object.")
        cost = 0 if endpoint.get("path") == "/v1/jobs/bing/search" and isinstance(parameters.get("cursor"), str) and re.fullmatch(r"bsw1_[A-Za-z0-9_-]{100,2043}", parameters["cursor"]) else endpoint.get("creditCost")
        pricing = endpoint.get("creditPricing")
        if endpoint.get("path") == "/v1/linkedin/company/people" and parameters.get("include") == "profile" and (not isinstance(pricing, dict) or pricing.get("type") != "profile_hydration"):
            raise SocialQueryError("Company profile enrichment requires refreshed pricing metadata.")
        resolved_pricing = isinstance(pricing, dict) and pricing.get("type") == "resolved_endpoint"
        if resolved_pricing:
            if endpoint.get("path") != "/v1/prism/lookup" or type(pricing.get("maxCredits")) is not int or not 1 <= pricing["maxCredits"] <= 9007199254740991:
                raise SocialQueryError("Invalid resolved pricing metadata.")
            cost = pricing["maxCredits"]
        elif isinstance(pricing, dict) and pricing.get("type") == "boolean_option":
            expected = {"/v1/tiktok/comment": (2, 6, "deep_scan"), "/v1/instagram/comment": (5, 15, "deep_scan"), "/v1/prism/crisis-radar": (15, 45, "confirm")}.get(endpoint.get("path"))
            if expected is None or pricing.get("parameter") != expected[2] or type(pricing.get("defaultCredits")) is not int or pricing["defaultCredits"] != expected[0] or type(pricing.get("enabledCredits")) is not int or pricing["enabledCredits"] != expected[1]:
                raise SocialQueryError("Invalid boolean-option pricing metadata.")
            raw = parameters.get(expected[2])
            if expected[2] in parameters and not (type(raw) is bool or type(raw) is str and raw in ("true", "false")):
                raise SocialQueryError(f"{expected[2]} must be true or false.")
            cost = expected[1] if raw is True or raw == "true" else expected[0]
        elif isinstance(pricing, dict) and pricing.get("type") == "returned_rows":
            expected_max = 25 if endpoint.get("path") in ("/v1/douyin/search", "/v1/douyin/profile/posts", "/v1/douyin/search/users") else 20 if endpoint.get("path") in ("/v1/xiaohongshu/search", "/v1/xiaohongshu/profile/posts", "/v1/xiaohongshu/trending") else None
            if expected_max is None or endpoint.get("method") != "GET" or pricing.get("limitParameter") != "limit" or any(type(pricing.get(k)) is not int or pricing[k] != v for k,v in {"creditsPerItem":5,"defaultLimit":10,"maxLimit":expected_max,"maxCredits":expected_max*5}.items()):
                raise SocialQueryError("Invalid returned-row pricing metadata.")
            raw = parameters.get("limit", 10)
            if type(raw) not in (str,int) or not re.fullmatch(r"[1-9][0-9]?", str(raw)) or int(raw) > expected_max:
                raise SocialQueryError(f"limit must be a canonical integer from 1 to {expected_max}.")
            cost = int(raw) * 5
        elif isinstance(pricing, dict) and pricing.get("type") == "reddit_omni":
            if endpoint.get("path") != "/v1/reddit/omni-search" or endpoint.get("method") != "GET" or any(type(pricing.get(k)) is not int or pricing[k] != v for k,v in {"baseCredits":1,"minimumCredits":5,"defaultThreads":8,"maxThreads":8,"maxCredits":9}.items()):
                raise SocialQueryError("Invalid Reddit Omni pricing metadata.")
            query, threads, raw = parameters.get("query"), parameters.get("threads", 8), parameters.get("include", "subreddits,comments")
            if not isinstance(query, str) or not query.strip() or len(query.encode("utf-16-le")) // 2 > 500 or re.search(r"[\x00-\x1f\x7f]", query) or type(threads) not in (int,str) or not re.fullmatch(r"[1-8]", str(threads)) or not isinstance(raw, str):
                raise SocialQueryError("Invalid Reddit Omni query, threads or include.")
            include = [s.strip() for s in raw.split(",")]
            if not include or len(set(include)) != len(include) or any(s not in ("subreddits", "comments") for s in include):
                raise SocialQueryError("Invalid Reddit Omni include.")
            cost = max(5, 1 + (int(threads) if "comments" in include else 0))
        elif isinstance(pricing, dict) and pricing.get("type") == "korea_gap":
            if endpoint.get("path") != "/v1/prism/korea-gap" or type(pricing.get("defaultCredits")) is not int or pricing["defaultCredits"] != 40 or type(pricing.get("webOnlyCredits")) is not int or pricing["webOnlyCredits"] != 15:
                raise SocialQueryError("Invalid Korea Gap pricing metadata.")
            raw = parameters.get("include")
            if "include" in parameters and not isinstance(raw, str):
                raise SocialQueryError("include must be a CSV string.")
            include = ["social", "quotes"] if raw is None else [] if raw == "" else [v.strip() for v in raw.split(",")]
            if len(set(include)) != len(include) or any(v not in ("social", "quotes") for v in include):
                raise SocialQueryError("include accepts social and quotes only.")
            query = parameters.get("query")
            if not isinstance(query, str) or not query.strip() or len(query.strip().encode("utf-16-le")) // 2 > 500 or re.search(r"[\x00-\x1f\x7f]", query):
                raise SocialQueryError("query must be 1–500 plain-text characters.")
            cost = 40 if "social" in include else 15
        elif isinstance(pricing, dict) and pricing.get("type") == "store_reviews":
            if endpoint.get("path") != "/v1/prism/app-reviews" or type(pricing.get("singleStoreCredits")) is not int or pricing["singleStoreCredits"] != 10 or type(pricing.get("bothStoresCredits")) is not int or pricing["bothStoresCredits"] != 15:
                raise SocialQueryError("Invalid store-review pricing metadata.")
            stores = ["google_play", "app_store"]
            query = parameters.get("query")
            if "query" in parameters and (not isinstance(query, str) or not query.strip() or len(query.encode("utf-16-le")) // 2 > 200 or re.search(r"[\x00-\x1f\x7f]", query)):
                raise SocialQueryError("Invalid review search query.")
            if "stores" in parameters and not isinstance(parameters["stores"], str):
                raise SocialQueryError("Invalid review stores.")
            selected = ([v.strip() for v in parameters["stores"].split(",")] if "stores" in parameters else stores if query else [v for v in stores if v + "_id" in parameters])
            if not selected or len(selected) > 2 or len(set(selected)) != len(selected) or any(v not in stores for v in selected) or any(v + "_id" in parameters and v not in selected for v in stores):
                raise SocialQueryError("Invalid review stores.")
            for store in selected:
                field = store + "_id"
                if field not in parameters:
                    if not query:
                        raise SocialQueryError("Supply selected store IDs or a review search query.")
                elif not isinstance(parameters[field], str) or not parameters[field].strip():
                    raise SocialQueryError("Supply selected store IDs or a review search query.")
            cost = 10 if len(selected) == 1 else 15
        elif isinstance(pricing, dict) and pricing.get("type") == "group_search":
            expected = {"baseCredits": 1, "creditsPerGroup": 1, "maxGroups": 10, "maxCredits": 12 if ("maxRelevanceCredits" in pricing or "relevanceRowsPerCredit" in pricing) else 11}
            if endpoint.get("path") != "/v1/facebook/search/groups" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid group-search pricing metadata.")
            relevance_pricing = "maxRelevanceCredits" in pricing or "relevanceRowsPerCredit" in pricing
            if relevance_pricing and (type(pricing.get("maxRelevanceCredits")) is not int or pricing["maxRelevanceCredits"] != 1 or type(pricing.get("relevanceRowsPerCredit")) is not int or pricing["relevanceRowsPerCredit"] != 25):
                raise SocialQueryError("Invalid group-search pricing metadata.")
            if "dryRunSupported" in pricing and (not relevance_pricing or pricing["dryRunSupported"] is not True):
                raise SocialQueryError("Invalid group-search pricing metadata.")
            query = parameters.get("query")
            if not isinstance(query, str) or not query.strip() or len(query.strip().encode("utf-16-le")) // 2 > 200 or re.search(r"[\x00-\x1f\x7f]", query) or re.search(r"\bsite\s*:", query, re.I):
                raise SocialQueryError("Invalid group-search query.")
            if "include" in parameters and parameters["include"] != "details":
                raise SocialQueryError("include accepts details only.")
            if "page" in parameters and (type(parameters["page"]) not in (int, str) or not re.fullmatch(r"[1-9][0-9]*", str(parameters["page"])) or int(parameters["page"]) > 100):
                raise SocialQueryError("page must be 1–100.")
            if "cursor" in parameters and ("page" in parameters or not isinstance(parameters["cursor"], str) or len(parameters["cursor"]) > 8000 or not re.fullmatch(r"fbgs1_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", parameters["cursor"])):
                raise SocialQueryError("Invalid group-search cursor.")
            controls = ["relevance", "relevance_threshold", "relevant_to", "judgments", "label"]
            if not relevance_pricing and any(k in parameters for k in controls):
                raise SocialQueryError("Group relevance requires refreshed pricing metadata.")
            if "judgments" in parameters and parameters["judgments"] not in ("on", "off") or "label" in parameters and parameters["label"] != "none" or "relevance" in parameters and parameters["relevance"] not in ("score", "filter"):
                raise SocialQueryError("Invalid group relevance controls.")
            off = parameters.get("judgments") == "off" or parameters.get("label") == "none"
            if off and "relevance" in parameters or parameters.get("label") == "none" and parameters.get("judgments") == "on" or ("relevance_threshold" in parameters or "relevant_to" in parameters) and "relevance" not in parameters:
                raise SocialQueryError("Invalid group relevance controls.")
            threshold = parameters.get("relevance_threshold")
            if "relevance_threshold" in parameters and (type(threshold) not in (str, int, float) or not re.fullmatch(r"(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)", str(threshold))):
                raise SocialQueryError("Invalid group relevance threshold.")
            topic = parameters.get("relevant_to")
            if "relevant_to" in parameters and (not isinstance(topic, str) or not topic.strip() or len(topic.strip().encode("utf-16-le")) // 2 > 200 or re.search(r"[\x00-\x1f\x7f]", topic)):
                raise SocialQueryError("Invalid group relevance topic.")
            if "dry_run" in parameters and (pricing.get("dryRunSupported") is not True or type(parameters["dry_run"]) not in (str, int) or str(parameters["dry_run"]) != "1"):
                raise SocialQueryError("dry_run=1 requires preview-capable pricing metadata.")
            cost = 0 if "dry_run" in parameters else (11 if parameters.get("include") == "details" else 1) + (1 if "relevant_to" in parameters else 0)
        elif isinstance(pricing, dict) and pricing.get("type") == "comment_leads":
            expected = {"defaultPosts": 30, "maxPosts": 90, "defaultOpen": 12, "maxOpen": 20, "defaultComments": 50, "maxComments": 200, "postsPerPage": 30, "commentsPerPage": 50, "labelsPerCredit": 25, "maxCredits": 243}
            if endpoint.get("path") != "/v1/prism/comment-leads" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid comment-leads pricing metadata.")
            values = []
            for name, fallback, maximum in [("posts", 30, 90), ("open", 12, 20), ("comments_per_post", 50, 200)]:
                raw = parameters.get(name, fallback)
                if type(raw) not in (int, str) or not re.fullmatch(r"[1-9][0-9]*", str(raw)) or int(raw) > maximum:
                    raise SocialQueryError(f"{name} is outside its supported range.")
                values.append(int(raw))
            for name, maximum in [("query", 100), ("offer", 300)]:
                value = parameters.get(name, parameters.get("query") if name == "offer" else None)
                if not isinstance(value, str) or not value.strip() or len(value.strip().encode("utf-16-le")) // 2 > maximum or re.search(r"[\x00-\x1f\x7f]", value):
                    raise SocialQueryError(f"Invalid {name}.")
            if "platform" in parameters and parameters["platform"] != "tiktok" or "include" in parameters and parameters["include"] != "askers":
                raise SocialQueryError("Invalid Comment Leads platform or include.")
            posts, opened, comments = values
            cost = (posts + 29) // 30 + opened * ((comments + 49) // 50 + (comments + 24) // 25)
        elif isinstance(pricing, dict) and pricing.get("type") == "investigation_budget":
            expected = {"defaultBudget": 40, "maxBudget": 200, "maxSteps": 12}
            if endpoint.get("path") != "/v1/prism/investigate" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid investigation pricing metadata.")
            raw, steps = parameters.get("budget", 40), parameters.get("max_steps", 8)
            for value, limit, name in [(raw, 200, "budget"), (steps, 12, "max_steps")]:
                if type(value) not in (int, str) or not re.fullmatch(r"[1-9][0-9]*", str(value)) or int(value) > limit:
                    raise SocialQueryError(f"{name} must be 1–{limit}.")
            goal = parameters.get("goal")
            if not isinstance(goal, str) or not goal.strip() or len(goal.strip()) > 500 or re.search(r"[\x00-\x1f\x7f]", goal):
                raise SocialQueryError("goal must be 1–500 plain-text characters.")
            cost = int(raw)
        elif isinstance(pricing, dict) and pricing.get("type") == "profile_hydration":
            linkedin = endpoint.get("path") == "/v1/linkedin/company/people"
            expected = {"baseCredits": 10, "creditsPerProfile": 4, "defaultLimit": 10, "maxLimit": 10} if linkedin else {"baseCredits": 5, "creditsPerProfile": 1, "defaultLimit": 20, "maxLimit": 80}
            if (not linkedin and endpoint.get("path") != "/v1/instagram/similar") or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid profile enrichment pricing metadata.")
            include = parameters.get("include")
            if "include" in parameters and include != "profile": raise SocialQueryError("include accepts only profile.")
            raw = parameters.get("limit", 10 if linkedin else 20 if include == "profile" else 80)
            if type(raw) not in (int, str) or not re.fullmatch(r"[1-9][0-9]?", str(raw)) or int(raw) > expected["maxLimit"]:
                raise SocialQueryError(f"limit must be 1–{expected['maxLimit']}.")
            cost = expected["baseCredits"] + (int(raw) * expected["creditsPerProfile"] if include == "profile" else 0)
        elif isinstance(pricing, dict) and pricing.get("type") == "org_repositories":
            expected = {"baseCredits": 1, "creditsPerRepo": 5, "defaultRepos": 5, "maxRepos": 10}
            if endpoint.get("path") != "/v1/prism/org-radar" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid organization pricing metadata.")
            raw = parameters.get("repos", 5)
            if type(raw) not in (int, str) or not re.fullmatch(r"(?:[1-9]|10)", str(raw)):
                raise SocialQueryError("repos must be an integer from 1 to 10.")
            include = parameters.get("include", "dossiers,rollup")
            if not isinstance(include, str): raise SocialQueryError("Invalid organization include.")
            parts = [x.strip() for x in include.split(",")]
            if any(x not in ("dossiers", "rollup") for x in parts) or len(set(parts)) != len(parts):
                raise SocialQueryError("Invalid organization include.")
            cost = 1 + (5 * int(raw) if "dossiers" in parts else 0)
        elif isinstance(pricing, dict) and pricing.get("type") == "enriched_reel_pages":
            expected = {"creditsPerPage": 5, "pageSize": 10, "maxLimit": 50}
            if endpoint.get("path") != "/v1/facebook/profile/reels/full" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid enriched reel pricing metadata.")
            raw = parameters.get("limit", 10)
            if type(raw) not in (int, str) or isinstance(raw, str) and not re.fullmatch(r"[1-9][0-9]*", raw) or not 1 <= int(raw) <= 50:
                raise SocialQueryError("limit must be an integer from 1 to 50.")
            cost = ((int(raw) + 9) // 10) * 5
        elif isinstance(pricing, dict) and pricing.get("type") == "creator_card":
            expected = {"baseCredits": 5, "includedPlatforms": 4, "extraPlatformCredits": 1, "maxPlatforms": 7}
            if endpoint.get("path") != "/v1/prism/creator-card" or any(type(pricing.get(k)) is not int or pricing[k] != v for k, v in expected.items()):
                raise SocialQueryError("Invalid creator-card pricing metadata.")
            raw = parameters.get("platforms", "tiktok,instagram,youtube,twitter")
            if not isinstance(raw, str): raise SocialQueryError("Invalid creator platforms.")
            platforms = {p.strip() for p in raw.split(",") if p.strip() in ("tiktok", "instagram", "youtube", "twitter", "threads", "bluesky", "truthsocial")}
            if not platforms: raise SocialQueryError("Select a supported creator platform.")
            cost = 5 + max(0, len(platforms) - 4)
        elif isinstance(pricing, dict) and pricing.get("type") == "comment_lookup_batch":
            if endpoint.get("path") != "/v1/prism/comment-lookup" or type(pricing.get("maxItems")) is not int or pricing["maxItems"] != 25 or type(pricing.get("maxCredits")) is not int or pricing["maxCredits"] != 100:
                raise SocialQueryError("Invalid comment batch pricing metadata.")
            # The server resolves valid items and enforces the explicit caller cap before debit.
            resolved_pricing, cost = True, 100
        elif isinstance(pricing, dict) and pricing.get("type") == "news_legs":
            expected={"baseCredits":2,"googleCreditsPerLeg":1,"bingArticlesPerCredit":5,"maxLegs":12}
            if endpoint.get("path")!="/v1/search/news" or any(type(pricing.get(k)) is not int or pricing[k]!=v for k,v in expected.items()):
                raise SocialQueryError("Invalid news pricing metadata.")
            countries_raw,engines_raw=parameters.get("countries","US"),parameters.get("engines","google")
            if not isinstance(countries_raw,str) or not isinstance(engines_raw,str): raise SocialQueryError("Invalid news countries or engines.")
            countries=list(dict.fromkeys(x.strip().upper() for x in countries_raw.split(",")))
            engines=list(dict.fromkeys(x.strip().lower() for x in engines_raw.split(",")))
            if not 1<=len(countries)<=12 or any(not re.fullmatch(r"[A-Z]{2}",x) for x in countries) or not engines or any(x not in ("google","bing") for x in engines): raise SocialQueryError("Invalid news countries or engines.")
            def bounded_integer(raw,minimum,maximum):
                if type(raw) not in (int,str) or isinstance(raw,str) and not re.fullmatch(r"[0-9]+",raw) or not minimum<=int(raw)<=maximum: raise SocialQueryError("Invalid news depth or max_legs.")
                return int(raw)
            depth=bounded_integer(parameters.get("depth",10),10,100)
            legs=min(len(countries),bounded_integer(parameters.get("max_legs",12),1,12))
            if depth%10: raise SocialQueryError("Invalid news depth.")
            cost=2+legs*((1 if "google" in engines else 0)+((min(depth,20)+4)//5 if "bing" in engines else 0))
        elif isinstance(pricing, dict) and pricing.get("type") == "shop_regions":
            if endpoint.get("path") != "/v1/tiktokshop/sales" or type(pricing.get("creditsPerRegion")) is not int or pricing["creditsPerRegion"] != 1 or type(pricing.get("maxRegions")) is not int or pricing["maxRegions"] != 8 or pricing.get("defaultRegion") != "US":
                raise SocialQueryError("Invalid shop market pricing metadata.")
            raw = parameters.get("regions", "US")
            if not isinstance(raw, str):
                raise SocialQueryError("Invalid shop markets.")
            regions = [r.strip().upper() for r in raw.split(",")]
            if not 1 <= len(regions) <= 8 or any(r not in ("US","GB","DE","FR","IT","ES","IE","ID","MY","MX","PH","SG","TH","VN","BR","JP") for r in regions):
                raise SocialQueryError("Invalid shop markets.")
            cost = len(set(regions))
        elif isinstance(pricing, dict) and pricing.get("type") == "board_plus_item":
            if endpoint.get("path") != "/v1/tiktok/videos/popular" or pricing.get("baseCredits") != 25 or pricing.get("creditsPerItem") != 1 or pricing.get("defaultLimit") != 20 or pricing.get("maxLimit") != 20:
                raise SocialQueryError("Invalid video-board pricing metadata.")
            raw = parameters.get("limit", 20)
            if not (type(raw) is int or isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw)) or not 1 <= int(raw) <= 20:
                raise SocialQueryError("Invalid video-board limit.")
            cost = 25 + int(raw)
        elif isinstance(pricing, dict) and pricing.get("type") == "trend_boards":
            if endpoint.get("path") != "/v1/tiktok/hashtags/popular" or pricing.get("creditsPerItem") != 2 or pricing.get("minimumCredits") != 6 or pricing.get("maxItems") != 48:
                raise SocialQueryError("Invalid trend-board pricing metadata.")
            cost = 96 if parameters.get("industry") == "all" else 6
        elif isinstance(pricing, dict) and pricing.get("type") == "post_stats_batch":
            if endpoint.get("path") != "/v1/prism/post-stats" or pricing.get("maxItems") != 100 or pricing.get("defaultCredits") != 1 or pricing.get("platformCredits") != {"instagram": 2, "linkedin": 5}:
                raise SocialQueryError("Invalid post-statistics pricing metadata.")
            urls = parameters.get("urls")
            if isinstance(urls, str):
                try:
                    urls = json.loads(urls)
                except (ValueError, TypeError):
                    raise SocialQueryError("Invalid post URLs.")
            if not isinstance(urls, list) or not 1 <= len(urls) <= 100 or any(not isinstance(url, str) or not url.strip() or len(url) > 2048 for url in urls):
                raise SocialQueryError("Invalid post URLs.")
            cost = 0
            for url in urls:
                try:
                    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
                except ValueError:
                    host = ""
                cost += 2 if host == "instagram.com" else 5 if host == "linkedin.com" else 1
        elif isinstance(pricing, dict) and pricing.get("type") == "job_batch":
            if endpoint.get("path") != "/v1/prism/jobs" or endpoint.get("method") != "POST" or pricing.get("maxItems") != 5000 or pricing.get("defaultCredits") != 1 or pricing.get("profileCredits") != {"linkedin": 5} or pricing.get("postCredits") != {"instagram": 2, "linkedin": 5}:
                raise SocialQueryError("Invalid job pricing metadata.")
            target = parameters.get("endpoint")
            if target not in ("prism/profiles", "prism/post-stats") or set(parameters) - {"endpoint", "items", "webhook", "include", "since"}:
                raise SocialQueryError("Unsupported job endpoint or options.")
            if "webhook" in parameters:
                _job_webhook(parameters["webhook"])
            with_posts = "include" in parameters
            if with_posts and (target != "prism/profiles" or parameters["include"] != "posts"):
                raise SocialQueryError("include must be posts for profile jobs.")
            if "since" in parameters:
                value = parameters["since"]
                try:
                    if not with_posts or not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2}))?", value):
                        raise ValueError()
                    # JavaScript rejects leap seconds/24:00 and offset hours >=24.
                    if len(value) > 10 and (int(value[11:13]) > 23 or int(value[14:16]) > 59 or int(value[17:19]) > 59 or value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59)):
                        raise ValueError()
                    datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    raise SocialQueryError("Invalid job since date.")
            items = parameters.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 5000:
                raise SocialQueryError("Jobs require an array of 1–5,000 items.")
            cost = 0
            for item in items:
                if not isinstance(item, dict) or "custom_id" in item and (not isinstance(item["custom_id"], str) or len(item["custom_id"]) > 200):
                    raise SocialQueryError("Invalid job item.")
                if target == "prism/profiles":
                    if set(item) - {"platform", "handle", "custom_id", "stop_at_id"} or not isinstance(item.get("platform"), str) or not isinstance(item.get("handle"), str) or not item["handle"].strip():
                        raise SocialQueryError("Invalid job profile.")
                    if "stop_at_id" in item and (not with_posts or not isinstance(item["stop_at_id"], str) or not item["stop_at_id"].strip() or len(item["stop_at_id"]) > 512 or re.search(r"[\x00-\x1f\x7f]", item["stop_at_id"])):
                        raise SocialQueryError("Invalid job stop_at_id.")
                    cost += (5 if item["platform"] == "linkedin" else 1) + (1 if with_posts and item["platform"] in ("instagram", "tiktok", "youtube", "twitter", "threads", "facebook") else 0)
                else:
                    if set(item) - {"url", "custom_id"} or not isinstance(item.get("url"), str) or len(item["url"]) > 2048:
                        raise SocialQueryError("Invalid job URL.")
                    try:
                        parsed = urllib.parse.urlsplit(item["url"])
                        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port:
                            raise ValueError()
                        host = parsed.hostname.lower().removeprefix("www.")
                    except ValueError:
                        raise SocialQueryError("Use a public HTTPS post URL.")
                    cost += 2 if host == "instagram.com" else 5 if host == "linkedin.com" else 1
        elif isinstance(pricing, dict) and pricing.get("type") == "linkedin_profile_urls":
            if endpoint.get("path") != "/v1/linkedin/profile/all" or type(pricing.get("maxItems")) is not int or pricing["maxItems"] != 10 or type(pricing.get("creditsPerProfile")) is not int or pricing["creditsPerProfile"] != 5:
                raise SocialQueryError("Invalid LinkedIn profile pricing metadata.")
            if ("url" in parameters) == ("urls" in parameters):
                raise SocialQueryError("Supply exactly one of url or urls.")
            raw = parameters.get("urls") if "urls" in parameters else parameters.get("url")
            if not isinstance(raw, str) or len(raw) > 16000 or re.search(r"[\x00-\x20\x7f]", raw):
                raise SocialQueryError("Invalid LinkedIn profile URLs.")
            inputs = raw.split(",") if "urls" in parameters else [raw]
            if not 1 <= len(inputs) <= 10:
                raise SocialQueryError("Supply one to ten canonical HTTPS LinkedIn profile URLs.")
            cost = len({_linkedin_profile_url(value) for value in inputs}) * 5
        elif isinstance(pricing, dict) and pricing.get("type") == "profile_batch":
            if endpoint.get("path") != "/v1/prism/profiles" or pricing.get("maxItems") != 50 or pricing.get("defaultCredits") != 1 or pricing.get("platformCredits") != {"linkedin": 5}:
                raise SocialQueryError("Invalid profile pricing metadata.")
            items = parameters.get("items")
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except (ValueError, TypeError):
                    raise SocialQueryError("Invalid profile items.")
            if not isinstance(items, list) or not 1 <= len(items) <= 50 or any(not isinstance(item, dict) or not isinstance(item.get("platform"), str) or not isinstance(item.get("handle"), str) or not item["handle"].strip() for item in items):
                raise SocialQueryError("Invalid profile items.")
            cost = sum(5 if item["platform"] == "linkedin" else 1 for item in items)
        elif isinstance(pricing, dict) and pricing.get("type") == "per_successful_input":
            if endpoint.get("path") != "/v1/youtube/transcripts" or pricing.get("arrayParameter") != "ids" or pricing.get("creditsPerItem") != 3 or pricing.get("maxItems") != 100:
                raise SocialQueryError("Invalid batch pricing metadata.")
            ids = parameters.get("ids")
            if isinstance(ids, str):
                try:
                    ids = json.loads(ids)
                except (ValueError, TypeError):
                    raise SocialQueryError("Invalid transcript IDs.")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", item) for item in ids):
                raise SocialQueryError("Invalid transcript IDs.")
            cost = len(ids) * 3
        elif pricing is not None:
            if not isinstance(pricing, dict) or pricing.get("type") != "per_item" or any(type(pricing.get(k)) is not int or pricing[k] < 1 for k in ("creditsPerItem", "defaultLimit", "maxLimit")) or pricing["defaultLimit"] > pricing["maxLimit"] or not isinstance(pricing.get("limitParameter"), str):
                raise SocialQueryError("Invalid metered pricing metadata.")
            raw = parameters.get(pricing["limitParameter"], pricing["defaultLimit"])
            if type(raw) not in (str, int) or not re.fullmatch(r"[0-9]+", str(raw)):
                raise SocialQueryError("Invalid metered request limit.")
            limit = int(raw)
            if limit < 1 or limit > pricing["maxLimit"]:
                raise SocialQueryError("Invalid metered request limit.")
            cost = limit * pricing["creditsPerItem"]
        if type(max_credits) is not int or max_credits < 0 or max_credits > 9007199254740991 or type(cost) is not int or cost < 0 or (not resolved_pricing and cost > max_credits):
            raise SocialQueryError("The maximum request charge exceeds max_credits, or the budget/cost is invalid.")
        specs = endpoint.get("params", [])
        allowed = {param["name"] for param in specs}
        if set(parameters) - allowed:
            raise SocialQueryError("Unknown parameter. Inspect the endpoint catalog.")
        def has_value(name):
            return name in parameters and parameters[name] is not None and parameters[name] != ""
        missing = [param["name"] for param in specs if param.get("required") and not has_value(param["name"])]
        if missing:
            raise SocialQueryError("Missing required parameters: " + ", ".join(missing))
        if any(not any(has_value(name) for name in group) for group in endpoint.get("oneOfParameters", [])):
            raise SocialQueryError("Supply at least one parameter from each alternative group.")
        query = dict(parameters)
        def path_value(match):
            name = match.group(1)
            if not has_value(name) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(parameters[name])):
                raise SocialQueryError("Invalid path parameter.")
            return str(query.pop(name))
        path = re.sub(r"\{([^}]+)\}", path_value, endpoint["path"])
        return self.request(endpoint["method"], path, query, idempotency_key=idempotency_key, max_credits=max_credits)
