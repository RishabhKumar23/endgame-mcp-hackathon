import os
import asyncio
import aiohttp
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment variables from .env if present
load_dotenv()

# MASA Data API base URL and API key
MASA_BASE_URL = os.getenv("MASA_BASE_URL", "https://data.dev.masalabs.ai")
MASA_API_KEY = os.getenv("MASA_DATA_API_KEY")

if MASA_API_KEY is None:
    raise EnvironmentError(
        "MASA_DATA_API_KEY not found in environment variables or .env file!"
    )

# Create the MCP server instance
mcp = FastMCP("CryptoSentimentServer", settings={})

# Common headers for MASA API
HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MASA_API_KEY}",
}


async def _post(session: aiohttp.ClientSession, endpoint: str, json_data: dict) -> dict:
    url = f"{MASA_BASE_URL}{endpoint}"
    async with session.post(url, json=json_data, headers=HEADERS) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _get(session: aiohttp.ClientSession, endpoint: str) -> dict:
    url = f"{MASA_BASE_URL}{endpoint}"
    async with session.get(url, headers=HEADERS) as resp:
        resp.raise_for_status()
        return await resp.json()


@mcp.tool()
async def search_tweets(crypto_name: str, max_results: int = 10) -> list[dict]:
    """
    Search Twitter for recent tweets about the given cryptocurrency via MASA API.
    Returns a list of tweet objects.
    """
    async with aiohttp.ClientSession() as session:
        # Initiate search job
        start_payload = {"query": crypto_name, "max_results": max_results}
        start_resp = await _post(session, "/api/v1/search/live/twitter", start_payload)
        job_id = start_resp.get("uuid")
        if not job_id:
            raise RuntimeError("Failed to start Twitter search job.")

        # Poll for job completion
        status = None
        for _ in range(30):  # up to ~30*2 sec = 60 secs
            status_resp = await _get(
                session, f"/api/v1/search/live/twitter/status/{job_id}"
            )
            status = status_resp.get("status")
            if status == "done":
                break
            await asyncio.sleep(2)

        if status != "done":
            raise TimeoutError(f"Twitter search job {job_id} did not complete in time.")

        # Fetch results
        result_list = await _get(
            session, f"/api/v1/search/live/twitter/result/{job_id}"
        )
        return result_list


@mcp.tool()
async def analyze_tweets(tweets: list[dict], crypto_name: str) -> str:
    """
    Analyze sentiment of provided tweets for the given cryptocurrency via MASA API.
    Returns formatted analysis string.
    """
    # Prepare text blob of tweets
    tweets_text = "\n".join(tweet.get("Content", "") for tweet in tweets)
    prompt = (
        f"Analyze sentiment for {crypto_name} from these tweets. Provide:\n"
        "1. Overall sentiment (positive/negative/neutral)\n"
        "2. Sentiment percentage breakdown\n"
        "3. Key positive/negative themes\n"
        "4. Notable emotional indicators"
    )

    async with aiohttp.ClientSession() as session:
        analysis_payload = {"tweets": tweets_text, "prompt": prompt}
        analysis_resp = await _post(
            session, "/api/v1/search/analysis", analysis_payload
        )
        # MASA analysis endpoint returns "result" field
        result_text = analysis_resp.get("result") or analysis_resp.get("analysis")
        if not result_text:
            raise RuntimeError("Failed to retrieve analysis from MASA API.")
        return result_text


@mcp.tool()
async def get_crypto_sentiment(crypto_name: str, max_results: int = 10) -> str:
    """
    High-level tool: fetch tweets for a cryptocurrency and analyze their sentiment.
    """
    # 1. Fetch tweets
    tweets = await search_tweets(crypto_name, max_results)
    # 2. Analyze sentiment
    analysis = await analyze_tweets(tweets, crypto_name)
    return analysis


if __name__ == "__main__":
    mcp.run()
