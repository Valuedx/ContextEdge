import asyncio
import os
import litellm
from contextedge.config import settings

async def check_vertex():
    # LiteLLM uses these env vars for Vertex AI
    if settings.google_application_credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
    if settings.location:
        os.environ["VERTEX_LOCATION"] = settings.location
        
    print(f"DEBUG: Testing Vertex AI with model: {settings.default_extraction_model}")
    print(f"DEBUG: Credentials path: {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')}")
    print(f"DEBUG: Location: {os.environ.get('VERTEX_LOCATION')}")
    
    try:
        # LiteLLM automatically handles Vertex if the model starts with 'vertex_ai/'
        response = await litellm.acompletion(
            model=settings.default_extraction_model,
            messages=[{"role": "user", "content": "Just reply with 'vertex-online' if you hear me."}],
            timeout=30
        )
        print(f"SUCCESS: Vertex AI is ACTIVE! Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"FAILURE: Vertex AI configuration error.")
        print(f"ERROR TYPE: {type(e).__name__}")
        print(f"ERROR: {str(e)}")

if __name__ == "__main__":
    asyncio.run(check_vertex())
