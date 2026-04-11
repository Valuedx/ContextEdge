import asyncio
import litellm
import os

# Set your API key for the script
os.environ["GOOGLE_API_KEY"] = "AIzaSyCn6GDg0ImmbHyNkWkrEenTaD4GzuFcyyw"

async def test_model(model_name):
    print(f"Testing model: {model_name}...")
    try:
        response = await litellm.acompletion(
            model=model_name,
            messages=[{"role": "user", "content": "Return the word 'OK'"}],
            max_tokens=5
        )
        print(f"  SUCCESS: {response.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"  FAILED: {type(e).__name__} - {str(e)[:100]}")
        return False

async def main():
    models = [
        "gemini/gemini-1.5-flash",
        "gemini/gemini-1.5-flash-001",
        "gemini/gemini-1.5-pro",
        "gemini/gemini-2.0-flash-exp",
        "gemini/gemini-2.5-flash"
    ]
    for m in models:
        await test_model(m)
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
