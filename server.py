import sys

sys.stdout.reconfigure(encoding="utf-8")
import asyncio
import aiohttp
from mcp.server.fastmcp import FastMCP
import re
import os
from dotenv import load_dotenv

load_dotenv()

# MASA Data API base URL
MASA_BASE_URL = "https://data.dev.masalabs.ai"

MASA_API_KEY = os.environ.get("MASA_API_KEY")

if MASA_API_KEY is None:
    raise EnvironmentError(
        "MASA_API_KEY not found in .env file or environment variables!"
    )

# Create the MCP server instance.
mcp = FastMCP("DocSearchContextServer")


@mcp.tool()
async def analyze_crypto_sentiment(crypto_names: str, max_results: int = 100) -> str:
    """
    Analyzes the sentiment of tweets related to specified cryptocurrencies using Masa's Twitter and analysis APIs.

    Parameters:
        crypto_names (str): Comma-separated list of cryptocurrency names (e.g., "Bitcoin, Ethereum")
        max_results (int): Maximum number of tweets to analyze (default: 100)

    Returns:
        str: The sentiment analysis result from Masa's AI model
    """
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MASA_API_KEY}",
    }

    try:
        # Process crypto names into search query
        cryptos = [name.strip() for name in crypto_names.split(",")]
        if not cryptos:
            return "Error: Please provide at least one cryptocurrency name"

        query = " OR ".join(cryptos)

        async with aiohttp.ClientSession() as session:
            # Step 1: Create Twitter search job
            payload = {"query": query, "max_results": max_results}
            async with session.post(
                f"{MASA_BASE_URL}/api/v1/search/live/twitter",
                headers=headers,
                json=payload,
            ) as response:
                if response.status != 200:
                    return f"Error creating search job: {await response.text()}"
                job_data = await response.json()
                job_id = job_data.get("uuid")
                if not job_id:
                    return "Failed to get job ID from Twitter search response"

            # Step 2: Poll job status with timeout
            max_attempts = 10
            for _ in range(max_attempts):
                async with session.get(
                    f"{MASA_BASE_URL}/api/v1/search/live/twitter/status/{job_id}",
                    headers=headers,
                ) as status_response:
                    if status_response.status != 200:
                        return f"Status check failed: {await status_response.text()}"

                    status_data = await status_response.json()
                    current_status = status_data.get("status", "").lower()

                    if current_status == "completed":
                        break
                    elif current_status in ["failed", "error"]:
                        return f"Search job failed: {status_data.get('error', 'Unknown error')}"

                    await asyncio.sleep(5)
            else:
                return "Error: Twitter search timed out"

            # Step 3: Fetch results
            async with session.get(
                f"{MASA_BASE_URL}/api/v1/search/live/twitter/result/{job_id}",
                headers=headers,
            ) as result_response:
                if result_response.status != 200:
                    return f"Error fetching results: {await result_response.text()}"

                results_data = await result_response.json()
                tweets = [
                    tweet.get("text", "") for tweet in results_data.get("results", [])
                ]
                if not tweets:
                    return "No tweets found for analysis"

            # Step 4: Analyze sentiment
            analysis_payload = {
                "tweets": "\n".join(tweets),
                "prompt": (
                    f"Analyze sentiment for {crypto_names} from these tweets. "
                    "Provide:\n1. Overall sentiment (positive/negative/neutral)\n"
                    "2. Sentiment percentage breakdown\n3. Key positive/negative themes\n"
                    "4. Notable emotional indicators\nFormat clearly with headings."
                ),
            }

            async with session.post(
                f"{MASA_BASE_URL}/api/v1/search/analysis",
                headers=headers,
                json=analysis_payload,
            ) as analysis_response:
                if analysis_response.status != 200:
                    return f"Analysis failed: {await analysis_response.text()}"

                analysis_data = await analysis_response.json()
                return analysis_data.get("result", "No analysis result returned")

    except Exception as e:
        return f"Error in sentiment analysis: {str(e)}"


if __name__ == "__main__":
    mcp.run()
