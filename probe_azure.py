"""Quick probe to find working Azure endpoint config and deployment names."""
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI

load_dotenv(Path(__file__).parent / ".env")

KEY      = os.getenv("AZURE_OPENAI_API_KEY", "")
ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")

DEPLOYMENTS = [
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4",
]

print(f"Endpoint: {ENDPOINT}")
print(f"Key set:  {'yes' if KEY else 'NO — check .env'}\n")

# Endpoints ending in /v1 use the standard OpenAI-compatible client (no api-version)
if ENDPOINT.rstrip("/").endswith("/v1"):
    print("Detected /v1 endpoint — using standard OpenAI client\n")
    client = OpenAI(api_key=KEY, base_url=ENDPOINT, timeout=10.0, max_retries=0)

    for dep in DEPLOYMENTS:
        try:
            resp = client.chat.completions.create(
                model=dep,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            print(f"  deployment '{dep}' — WORKS  (reply: {resp.choices[0].message.content!r})")
        except Exception as e:
            msg = str(e)
            if "404" in msg or "not found" in msg.lower():
                print(f"  deployment '{dep}' — not found")
            elif "401" in msg or "403" in msg:
                print(f"  deployment '{dep}' — auth error: {msg[:100]}")
            else:
                print(f"  deployment '{dep}' — {msg[:100]}")

else:
    # Classic AzureOpenAI endpoint — try API versions
    VERSIONS = [
        "2025-04-01-preview",
        "2025-03-01-preview",
        "2025-01-01-preview",
        "2024-12-01-preview",
        "2024-10-01-preview",
    ]
    base = ENDPOINT
    if base.endswith("/openai"):
        base = base[:-len("/openai")]
    print(f"Detected classic Azure endpoint — base: {base}\n")

    found_version = None
    for version in VERSIONS:
        try:
            c = AzureOpenAI(api_key=KEY, azure_endpoint=base,
                            api_version=version, timeout=10.0, max_retries=0)
            c.chat.completions.create(
                model=DEPLOYMENTS[0],
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            print(f"  {version} — WORKS with '{DEPLOYMENTS[0]}'")
            found_version = version
            break
        except Exception as e:
            msg = str(e)
            if "404" in msg or "not found" in msg.lower():
                print(f"  {version} — version OK, deployment '{DEPLOYMENTS[0]}' not found")
                found_version = version
                break
            elif "401" in msg or "403" in msg:
                print(f"  {version} — auth error: {msg[:100]}")
                break
            else:
                print(f"  {version} — {msg[:80]}")

    if found_version:
        client = AzureOpenAI(api_key=KEY, azure_endpoint=base,
                             api_version=found_version, timeout=10.0, max_retries=0)
        print(f"\nTesting deployments with version {found_version}:\n")
        for dep in DEPLOYMENTS:
            try:
                resp = client.chat.completions.create(
                    model=dep,
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=5,
                )
                print(f"  '{dep}' — WORKS")
            except Exception as e:
                msg = str(e)
                if "404" in msg or "not found" in msg.lower():
                    print(f"  '{dep}' — not found")
                else:
                    print(f"  '{dep}' — {msg[:100]}")
