import asyncio
import os
import litellm
from dotenv import load_dotenv

async def check_quota():
    # Load from the root .env
    load_dotenv(dotenv_path="../.env")
    api_key = os.getenv("GOOGLE_API_KEY")
    os.environ["GOOGLE_API_KEY"] = api_key
    
    print(f"DEBUG: Testing Gemini with key: {api_key[:15]}...")
    
    try:
        response = await litellm.acompletion(
            model="gemini/gemini-flash-latest",
            messages=[{"role": "user", "content": "Just say 'active' if you are up."}],
            timeout=30
        )
        print(f"SUCCESS: Gemini is ACTIVE! Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"FAILURE: Gemini API error.")
        print(f"ERROR: {str(e)}")

if __name__ == "__main__":
    asyncio.run(check_quota())
