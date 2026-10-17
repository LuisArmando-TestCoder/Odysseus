"""
scrapebadger.py

ScrapeBadger API client — a unified web-scraping + structured-data API.

Base URL: https://scrapebadger.com/v1
Auth:     x-api-key header (case-insensitive)

This module is the single integration point for every ScrapeBadger scraper:
web scraping, Google (16 products incl. AI Mode), Reddit, YouTube, TikTok,
LinkedIn, Instagram, Facebook, and Twitter/X.

The API key is read from the `scrapebadger_api_key` setting (set in the
Settings panel) with an env fallback of `SCRAPEBADGER_API_KEY`.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://scrapebadger.com/v1"
REQUEST_TIMEOUT = 60  # AI Mode can take ~25s; give headroom

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Return the ScrapeBadger API key from settings, falling back to env."""
    try:
        from src.settings import load_settings
        val = (load_settings().get("scrapebadger_api_key") or "").strip()
        if val:
            return val
    except Exception:
        pass
    return (os.environ.get("SCRAPEBADGER_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(get_api_key())


# ---------------------------------------------------------------------------
# Core request helper
# ---------------------------------------------------------------------------

def _sb_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Perform a ScrapeBadger request. Returns (data, error).

    On success returns the parsed JSON body and None. On failure returns
    (None, error_message). Rate-limit / credit headers are logged.
    """
    api_key = get_api_key()
    if not api_key:
        return None, "ScrapeBadger API key is not configured (set scrapebadger_api_key in Settings)"

    url = BASE_URL + path
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    try:
        if method.upper() == "POST":
            resp = httpx.post(
                url, headers=headers, params=params, json=json_body,
                timeout=REQUEST_TIMEOUT,
            )
        else:
            resp = httpx.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)

        credits = resp.headers.get("x-credits-used")
        rl_remaining = resp.headers.get("x-ratelimit-remaining")
        cache = resp.headers.get("x-cache")
        if credits is not None:
            logger.info("ScrapeBadger %s %s -> credits=%s cache=%s rl_remaining=%s",
                        method.upper(), path, credits, cache, rl_remaining)

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after", "unknown")
            return None, f"ScrapeBadger rate limit exceeded (retry-after {retry_after}s)"
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("detail") or body.get("error") or ""
            except Exception:
                detail = resp.text[:200]
            return None, f"ScrapeBadger {resp.status_code}: {detail or resp.reason_phrase}"

        return resp.json(), None
    except httpx.TimeoutException:
        return None, f"ScrapeBadger timed out after {REQUEST_TIMEOUT}s"
    except httpx.RequestError as e:
        return None, f"ScrapeBadger network error: {e}"
    except json.JSONDecodeError as e:
        return None, f"ScrapeBadger returned invalid JSON: {e}"


# ---------------------------------------------------------------------------
# Google AI Mode (the "search tool")
# ---------------------------------------------------------------------------

def google_ai_mode(query: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Ask Google AI Mode (udm=50) and return the structured answer.

    Returns the raw payload: text_blocks (prose/headings/lists/tables),
    references (sources), a compact `markdown` rendering, and answer_html.
    """
    return _sb_request("GET", "/google/ai-mode/search", params={"q": query})


def google_ai_mode_markdown(query: str) -> Tuple[Optional[str], Optional[str]]:
    """Convenience: return just the compact Markdown answer + references."""
    data, err = google_ai_mode(query)
    if err:
        return None, err
    md = data.get("markdown") or ""
    refs = data.get("references") or []
    if refs:
        md += "\n\nSources:\n"
        for r in refs:
            title = r.get("title") or ""
            link = r.get("link") or ""
            if link:
                md += f"- {title} ({link})\n"
            elif title:
                md += f"- {title}\n"
    return md, None


# ---------------------------------------------------------------------------
# Generic scraper dispatcher
# ---------------------------------------------------------------------------
#
# Each route maps (source, endpoint) -> (method, path_template, path_params).
# `path_params` names the params that are interpolated into the URL path
# (e.g. {subreddit}); everything else is sent as a query string (GET) or
# JSON body (POST).

_ROUTES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "web": {
        "scrape": {"method": "POST", "path": "/web/scrape", "body": True},
        "detect": {"method": "POST", "path": "/web/detect", "body": True},
    },
    "google": {
        "search":       {"method": "GET", "path": "/google/search"},
        "ai_mode":      {"method": "GET", "path": "/google/ai-mode/search"},
        "images":       {"method": "GET", "path": "/google/images/search"},
        "videos":       {"method": "GET", "path": "/google/videos/search"},
        "autocomplete": {"method": "GET", "path": "/google/autocomplete"},
        "lens":         {"method": "GET", "path": "/google/lens/search"},
        # Maps
        "maps":         {"method": "GET", "path": "/google/maps/search"},
        "maps_place":   {"method": "GET", "path": "/google/maps/place"},
        "maps_reviews": {"method": "GET", "path": "/google/maps/reviews"},
        "maps_photos":  {"method": "GET", "path": "/google/maps/photos"},
        "maps_posts":   {"method": "GET", "path": "/google/maps/posts"},
        # Shopping
        "shopping":         {"method": "GET", "path": "/google/shopping/search"},
        "shopping_product": {"method": "GET", "path": "/google/shopping/product"},
        "shopping_click":   {"method": "GET", "path": "/google/shopping/product/click"},
        # News & content
        "news":          {"method": "GET", "path": "/google/news/search"},
        "news_topics":   {"method": "GET", "path": "/google/news/topics"},
        "news_trending": {"method": "GET", "path": "/google/news/trending"},
        "scholar":       {"method": "GET", "path": "/google/scholar/search"},
        "patents":       {"method": "GET", "path": "/google/patents/search"},
        "patents_detail": {"method": "GET", "path": "/google/patents/detail"},
        # Hotels
        "hotels":         {"method": "GET", "path": "/google/hotels/search"},
        "hotels_details": {"method": "GET", "path": "/google/hotels/details"},
        # Trends
        "trends":             {"method": "GET", "path": "/google/trends/search"},
        "trends_trending":    {"method": "GET", "path": "/google/trends/trending-now"},
        "trends_autocomplete": {"method": "GET", "path": "/google/trends/autocomplete"},
        # Jobs / finance / products
        "jobs":     {"method": "GET", "path": "/google/jobs/search"},
        "finance":  {"method": "GET", "path": "/google/finance/quote"},
        "products": {"method": "GET", "path": "/google/products/detail"},
    },
    "reddit": {
        # Search
        "search_posts":      {"method": "GET", "path": "/reddit/search/posts"},
        "search_subreddits": {"method": "GET", "path": "/reddit/search/subreddits"},
        "search_users":      {"method": "GET", "path": "/reddit/search/users"},
        "domain_posts":      {"method": "GET", "path": "/reddit/domains/{domain}/posts", "path_params": ["domain"]},
        # Posts
        "trending":         {"method": "GET", "path": "/reddit/posts/trending"},
        "post":             {"method": "GET", "path": "/reddit/posts/{post_id}", "path_params": ["post_id"]},
        "post_comments":    {"method": "GET", "path": "/reddit/posts/{post_id}/comments", "path_params": ["post_id"]},
        "post_duplicates":  {"method": "GET", "path": "/reddit/posts/{post_id}/duplicates", "path_params": ["post_id"]},
        # Subreddits
        "subreddit":        {"method": "GET", "path": "/reddit/subreddits/{subreddit}", "path_params": ["subreddit"]},
        "subreddit_posts":  {"method": "GET", "path": "/reddit/subreddits/{subreddit}/posts", "path_params": ["subreddit"]},
        "subreddit_rules":  {"method": "GET", "path": "/reddit/subreddits/{subreddit}/rules", "path_params": ["subreddit"]},
        "subreddit_wiki":   {"method": "GET", "path": "/reddit/subreddits/{subreddit}/wiki", "path_params": ["subreddit"]},
        "subreddit_wiki_page": {"method": "GET", "path": "/reddit/subreddits/{subreddit}/wiki/{page}", "path_params": ["subreddit", "page"]},
        "subreddits_popular": {"method": "GET", "path": "/reddit/subreddits/popular"},
        "subreddits_new":     {"method": "GET", "path": "/reddit/subreddits/new"},
        # Users
        "user":             {"method": "GET", "path": "/reddit/users/{username}", "path_params": ["username"]},
        "user_posts":       {"method": "GET", "path": "/reddit/users/{username}/posts", "path_params": ["username"]},
        "user_comments":    {"method": "GET", "path": "/reddit/users/{username}/comments", "path_params": ["username"]},
        "user_moderated":   {"method": "GET", "path": "/reddit/users/{username}/moderated", "path_params": ["username"]},
        "user_trophies":    {"method": "GET", "path": "/reddit/users/{username}/trophies", "path_params": ["username"]},
    },
    "youtube": {
        # Search & discovery
        "search":          {"method": "GET", "path": "/youtube/search"},
        "autocomplete":    {"method": "GET", "path": "/youtube/autocomplete"},
        "trending":        {"method": "GET", "path": "/youtube/trending"},
        "trending_shorts": {"method": "GET", "path": "/youtube/trending/shorts"},
        "hashtag":         {"method": "GET", "path": "/youtube/hashtags/{tag}", "path_params": ["tag"]},
        "home":            {"method": "GET", "path": "/youtube/home"},
        "music_search":    {"method": "GET", "path": "/youtube/music/search"},
        # Videos & shorts
        "video":           {"method": "GET", "path": "/youtube/videos/{video_id}", "path_params": ["video_id"]},
        "videos_batch":    {"method": "POST", "path": "/youtube/videos/batch", "body": True},
        "related":         {"method": "GET", "path": "/youtube/videos/{video_id}/related", "path_params": ["video_id"]},
        "video_comments":  {"method": "GET", "path": "/youtube/videos/{video_id}/comments", "path_params": ["video_id"]},
        "comment_replies": {"method": "GET", "path": "/youtube/videos/{video_id}/comments/{comment_id}/replies", "path_params": ["video_id", "comment_id"]},
        "transcript":      {"method": "GET", "path": "/youtube/videos/{video_id}/transcript", "path_params": ["video_id"]},
        "captions":        {"method": "GET", "path": "/youtube/videos/{video_id}/captions", "path_params": ["video_id"]},
        "streams":         {"method": "GET", "path": "/youtube/videos/{video_id}/streams", "path_params": ["video_id"]},
        "live_chat":       {"method": "GET", "path": "/youtube/videos/{video_id}/live_chat", "path_params": ["video_id"]},
        "short":           {"method": "GET", "path": "/youtube/shorts/{video_id}", "path_params": ["video_id"]},
        "shorts_by_sound": {"method": "GET", "path": "/youtube/shorts/by_sound/{sound_id}", "path_params": ["sound_id"]},
        # Channels
        "channel":             {"method": "GET", "path": "/youtube/channels/{channel_id}", "path_params": ["channel_id"]},
        "channel_videos":      {"method": "GET", "path": "/youtube/channels/{channel_id}/videos", "path_params": ["channel_id"]},
        "channel_shorts":      {"method": "GET", "path": "/youtube/channels/{channel_id}/shorts", "path_params": ["channel_id"]},
        "channel_streams":     {"method": "GET", "path": "/youtube/channels/{channel_id}/streams", "path_params": ["channel_id"]},
        "channel_playlists":   {"method": "GET", "path": "/youtube/channels/{channel_id}/playlists", "path_params": ["channel_id"]},
        "channel_community":   {"method": "GET", "path": "/youtube/channels/{channel_id}/community", "path_params": ["channel_id"]},
        "channel_about":       {"method": "GET", "path": "/youtube/channels/{channel_id}/about", "path_params": ["channel_id"]},
        "channel_subscribers": {"method": "GET", "path": "/youtube/channels/{channel_id}/subscriber_count", "path_params": ["channel_id"]},
        "channel_search":      {"method": "GET", "path": "/youtube/channels/{channel_id}/search", "path_params": ["channel_id"]},
        "channel_resolve":     {"method": "GET", "path": "/youtube/channels/resolve"},
        # Playlists
        "playlist":       {"method": "GET", "path": "/youtube/playlists/{playlist_id}", "path_params": ["playlist_id"]},
        "playlist_items": {"method": "GET", "path": "/youtube/playlists/{playlist_id}/items", "path_params": ["playlist_id"]},
        "mix":            {"method": "GET", "path": "/youtube/mixes/{mix_id}", "path_params": ["mix_id"]},
        # Community & reference
        "post":          {"method": "GET", "path": "/youtube/posts/{post_id}", "path_params": ["post_id"]},
        "post_comments": {"method": "GET", "path": "/youtube/posts/{post_id}/comments", "path_params": ["post_id"]},
        "oembed":        {"method": "GET", "path": "/youtube/oembed"},
        "categories":    {"method": "GET", "path": "/youtube/categories"},
        "languages":     {"method": "GET", "path": "/youtube/languages"},
        "regions":       {"method": "GET", "path": "/youtube/regions"},
        "markets":       {"method": "GET", "path": "/youtube/markets"},
    },
    "tiktok": {
        # Profiles & videos
        "user":           {"method": "GET", "path": "/tiktok/users/{username}", "path_params": ["username"]},
        "user_videos":    {"method": "GET", "path": "/tiktok/users/{username}/videos", "path_params": ["username"]},
        "user_reposts":   {"method": "GET", "path": "/tiktok/users/{username}/reposts", "path_params": ["username"]},
        # Videos & comments
        "video":           {"method": "GET", "path": "/tiktok/videos/{video_id}", "path_params": ["video_id"]},
        "video_comments":  {"method": "GET", "path": "/tiktok/videos/{video_id}/comments", "path_params": ["video_id"]},
        "related":         {"method": "GET", "path": "/tiktok/videos/{video_id}/related", "path_params": ["video_id"]},
        "transcript":      {"method": "GET", "path": "/tiktok/videos/{video_id}/transcript", "path_params": ["video_id"]},
        "comment_replies": {"method": "GET", "path": "/tiktok/comments/{comment_id}/replies", "path_params": ["comment_id"]},
        "oembed":          {"method": "GET", "path": "/tiktok/oembed"},
        # Hashtags & music
        "hashtag":        {"method": "GET", "path": "/tiktok/hashtags/{name}", "path_params": ["name"]},
        "hashtag_videos": {"method": "GET", "path": "/tiktok/hashtags/{name}/videos", "path_params": ["name"]},
        "music":          {"method": "GET", "path": "/tiktok/music/{music_id}", "path_params": ["music_id"]},
        "music_videos":   {"method": "GET", "path": "/tiktok/music/{music_id}/videos", "path_params": ["music_id"]},
        # Search
        "search":         {"method": "GET", "path": "/tiktok/search"},
        "search_users":   {"method": "GET", "path": "/tiktok/search/users"},
        "search_videos":  {"method": "GET", "path": "/tiktok/search/videos"},
        "search_hashtags": {"method": "GET", "path": "/tiktok/search/hashtags"},
        # Trending
        "trending":          {"method": "GET", "path": "/tiktok/trending/videos"},
        "trending_hashtags": {"method": "GET", "path": "/tiktok/trending/hashtags"},
        "trending_songs":    {"method": "GET", "path": "/tiktok/trending/songs"},
        # TikTok Shop
        "shop_bestsellers": {"method": "GET", "path": "/tiktok/shop/bestsellers"},
        "shop_categories":  {"method": "GET", "path": "/tiktok/shop/categories"},
        "shop_category":    {"method": "GET", "path": "/tiktok/shop/categories/{category_id}", "path_params": ["category_id"]},
        "shop_search":      {"method": "GET", "path": "/tiktok/shop/search"},
        "shop_mall":        {"method": "GET", "path": "/tiktok/shop/mall"},
        "shop_store":       {"method": "GET", "path": "/tiktok/shop/stores/{seller_id}", "path_params": ["seller_id"]},
        "shop_product":     {"method": "GET", "path": "/tiktok/shop/products/{product_id}", "path_params": ["product_id"]},
        "shop_reviews":     {"method": "GET", "path": "/tiktok/shop/products/{product_id}/reviews", "path_params": ["product_id"]},
        # Ad transparency
        "ads_search":      {"method": "GET", "path": "/tiktok/ads/search"},
        "ads_advertisers": {"method": "GET", "path": "/tiktok/ads/advertisers"},
        "ad":              {"method": "GET", "path": "/tiktok/ads/{ad_id}", "path_params": ["ad_id"]},
        # Reference
        "regions": {"method": "GET", "path": "/tiktok/regions"},
    },
    "linkedin": {
        "jobs_search": {"method": "GET", "path": "/linkedin/jobs/search"},
        "job":         {"method": "GET", "path": "/linkedin/jobs/{job_id}", "path_params": ["job_id"]},
        "company":     {"method": "GET", "path": "/linkedin/companies/{universal_name}", "path_params": ["universal_name"]},
        "company_jobs": {"method": "GET", "path": "/linkedin/companies/{company_id}/jobs", "path_params": ["company_id"]},
        "school":      {"method": "GET", "path": "/linkedin/schools/{universal_name}", "path_params": ["universal_name"]},
        "profile":     {"method": "GET", "path": "/linkedin/profiles/{public_id}", "path_params": ["public_id"]},
        "post":        {"method": "GET", "path": "/linkedin/posts/{post_slug}", "path_params": ["post_slug"]},
        "article":     {"method": "GET", "path": "/linkedin/articles/{article_slug}", "path_params": ["article_slug"]},
        "course":      {"method": "GET", "path": "/linkedin/learning/{course_slug}", "path_params": ["course_slug"]},
        "geo_suggest": {"method": "GET", "path": "/linkedin/geo/suggest"},
    },
    "instagram": {
        "health":          {"method": "GET", "path": "/instagram/health"},
        "user":            {"method": "GET", "path": "/instagram/users/{username}", "path_params": ["username"]},
        "user_posts":      {"method": "GET", "path": "/instagram/users/{username}/posts", "path_params": ["username"]},
        "user_reels":      {"method": "GET", "path": "/instagram/users/{username}/reels", "path_params": ["username"]},
        "user_tagged":     {"method": "GET", "path": "/instagram/users/{username}/tagged", "path_params": ["username"]},
        "user_followers":  {"method": "GET", "path": "/instagram/users/{username}/followers", "path_params": ["username"]},
        "user_following":  {"method": "GET", "path": "/instagram/users/{username}/following", "path_params": ["username"]},
        "user_stories":    {"method": "GET", "path": "/instagram/users/{username}/stories", "path_params": ["username"]},
        "user_highlights": {"method": "GET", "path": "/instagram/users/{username}/highlights", "path_params": ["username"]},
        "media":           {"method": "GET", "path": "/instagram/media/{code}", "path_params": ["code"]},
        "media_comments":  {"method": "GET", "path": "/instagram/media/{code}/comments", "path_params": ["code"]},
        "search_hashtags": {"method": "GET", "path": "/instagram/search/hashtags"},
    },
    "facebook": {
        # Marketplace
        "marketplace":            {"method": "GET", "path": "/facebook/marketplace/search"},
        "marketplace_category":   {"method": "GET", "path": "/facebook/marketplace/category/{category}", "path_params": ["category"]},
        "marketplace_item":       {"method": "GET", "path": "/facebook/marketplace/item/{item_id}", "path_params": ["item_id"]},
        "marketplace_locations":  {"method": "GET", "path": "/facebook/marketplace/locations"},
        "marketplace_categories": {"method": "GET", "path": "/facebook/marketplace/categories"},
        # Search (all tabs)
        "search":     {"method": "GET", "path": "/facebook/search"},
        "search_tab": {"method": "GET", "path": "/facebook/search/{tab}", "path_params": ["tab"]},
        # Pages & profiles
        "page":         {"method": "GET", "path": "/facebook/pages/{identifier}", "path_params": ["identifier"]},
        "page_posts":   {"method": "GET", "path": "/facebook/pages/{identifier}/posts", "path_params": ["identifier"]},
        "profile":      {"method": "GET", "path": "/facebook/profiles/{identifier}", "path_params": ["identifier"]},
        "profile_posts": {"method": "GET", "path": "/facebook/profiles/{identifier}/posts", "path_params": ["identifier"]},
        # Groups
        "group":       {"method": "GET", "path": "/facebook/groups/{group_id}", "path_params": ["group_id"]},
        "group_posts": {"method": "GET", "path": "/facebook/groups/{group_id}/posts", "path_params": ["group_id"]},
        # Posts & comments
        "post":          {"method": "GET", "path": "/facebook/posts/{post_id}", "path_params": ["post_id"]},
        "post_comments": {"method": "GET", "path": "/facebook/posts/{post_id}/comments", "path_params": ["post_id"]},
        # Ad Library
        "ads":             {"method": "GET", "path": "/facebook/ads/search"},
        "ad":              {"method": "GET", "path": "/facebook/ads/{ad_archive_id}", "path_params": ["ad_archive_id"]},
        "ads_pages_search": {"method": "GET", "path": "/facebook/ads/pages/search"},
        "ads_page_info":    {"method": "GET", "path": "/facebook/ads/pages/{page_id}", "path_params": ["page_id"]},
    },
    "airbnb": {
        "health":              {"method": "GET", "path": "/airbnb/health"},
        "search":              {"method": "GET", "path": "/airbnb/search"},
        "listing":             {"method": "GET", "path": "/airbnb/listings/{room_id}", "path_params": ["room_id"]},
        "listing_calendar":    {"method": "GET", "path": "/airbnb/listings/{room_id}/calendar", "path_params": ["room_id"]},
        "listing_reviews":     {"method": "GET", "path": "/airbnb/listings/{room_id}/reviews", "path_params": ["room_id"]},
        "experiences":         {"method": "GET", "path": "/airbnb/experiences"},
        "experience":          {"method": "GET", "path": "/airbnb/experiences/{experience_id}", "path_params": ["experience_id"]},
    },
    "twitter": {
        # Tweets
        "tweet":                 {"method": "GET", "path": "/twitter/tweets/{tweet_id}", "path_params": ["tweet_id"]},
        "tweet_replies":         {"method": "GET", "path": "/twitter/tweets/{tweet_id}/replies", "path_params": ["tweet_id"]},
        "tweet_retweets":        {"method": "GET", "path": "/twitter/tweets/{tweet_id}/retweets", "path_params": ["tweet_id"]},
        "tweet_likes":           {"method": "GET", "path": "/twitter/tweets/{tweet_id}/likes", "path_params": ["tweet_id"]},
        "tweet_edit_history":    {"method": "GET", "path": "/twitter/tweets/{tweet_id}/edit_history", "path_params": ["tweet_id"]},
        "tweet_community_notes": {"method": "GET", "path": "/twitter/tweets/{tweet_id}/community_notes", "path_params": ["tweet_id"]},
        "search":                {"method": "GET", "path": "/twitter/search"},
        "advanced_search":       {"method": "GET", "path": "/twitter/search/advanced"},
        # Users
        "user":            {"method": "GET", "path": "/twitter/users/{username}", "path_params": ["username"]},
        "user_tweets":     {"method": "GET", "path": "/twitter/users/{username}/tweets", "path_params": ["username"]},
        "user_replies":    {"method": "GET", "path": "/twitter/users/{username}/replies", "path_params": ["username"]},
        "user_media":      {"method": "GET", "path": "/twitter/users/{username}/media", "path_params": ["username"]},
        "user_likes":      {"method": "GET", "path": "/twitter/users/{username}/likes", "path_params": ["username"]},
        "user_followers":  {"method": "GET", "path": "/twitter/users/{username}/followers", "path_params": ["username"]},
        "user_following":  {"method": "GET", "path": "/twitter/users/{username}/following", "path_params": ["username"]},
        "user_mentions":   {"method": "GET", "path": "/twitter/users/{username}/mentions", "path_params": ["username"]},
        "user_articles":   {"method": "GET", "path": "/twitter/users/{username}/articles", "path_params": ["username"]},
        "users_batch":     {"method": "GET", "path": "/twitter/users/batch"},
        # Lists
        "list":        {"method": "GET", "path": "/twitter/lists/{list_id}", "path_params": ["list_id"]},
        "list_tweets": {"method": "GET", "path": "/twitter/lists/{list_id}/tweets", "path_params": ["list_id"]},
        "list_search": {"method": "GET", "path": "/twitter/lists/search"},
        # Communities
        "community":        {"method": "GET", "path": "/twitter/communities/{community_id}", "path_params": ["community_id"]},
        "community_members": {"method": "GET", "path": "/twitter/communities/{community_id}/members", "path_params": ["community_id"]},
        "community_tweets":  {"method": "GET", "path": "/twitter/communities/{community_id}/tweets", "path_params": ["community_id"]},
        # Trends
        "trends":        {"method": "GET", "path": "/twitter/trends"},
        "trends_place":  {"method": "GET", "path": "/twitter/trends/{woeid}", "path_params": ["woeid"]},
        # Geo
        "geo_search": {"method": "GET", "path": "/twitter/geo/search"},
        "geo_place":   {"method": "GET", "path": "/twitter/geo/{place_id}", "path_params": ["place_id"]},
        # Spaces
        "space":        {"method": "GET", "path": "/twitter/spaces/{space_id}", "path_params": ["space_id"]},
        "space_search": {"method": "GET", "path": "/twitter/spaces/search"},
    },
}


def list_sources() -> Dict[str, list]:
    """Return {source: [endpoint, ...]} for tool discovery / help."""
    return {src: sorted(routes.keys()) for src, routes in _ROUTES.items()}


def scrape(source: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Dispatch a generic scraper call.

    `source`   — one of the platform keys in _ROUTES (web, google, reddit, ...).
    `endpoint` — an action name within that source (search, user, video, ...).
    `params`   — dict of query/body params. Path params (e.g. subreddit,
                 video_id, username) are interpolated into the URL; the rest
                 are sent as query string (GET) or JSON body (POST).
    """
    params = params or {}
    src_routes = _ROUTES.get(source)
    if not src_routes:
        return None, f"Unknown ScrapeBadger source '{source}'. Available: {', '.join(_ROUTES.keys())}"
    route = src_routes.get(endpoint)
    if not route:
        return None, f"Unknown '{source}' endpoint '{endpoint}'. Available: {', '.join(src_routes.keys())}"

    method = route["method"]
    path = route["path"]
    path_params = route.get("path_params", [])
    is_body = route.get("body", False)

    # Interpolate path params; drop them from the query/body.
    query_or_body = dict(params)
    for p in path_params:
        val = query_or_body.pop(p, None)
        if val is None:
            return None, f"Missing required path param '{p}' for {source}.{endpoint}"
        path = path.replace("{" + p + "}", str(val))

    if is_body:
        return _sb_request(method, path, json_body=query_or_body)
    return _sb_request(method, path, params=query_or_body)