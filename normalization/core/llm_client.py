from openai import OpenAI

from core.config import settings


client = OpenAI(
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,
)


def ask_llm(system_prompt: str, user_prompt: str) -> str:
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0,
        stream=False,
    )

    content = response.choices[0].message.content

    if content is None:
        raise ValueError("LLM returned empty response")

    return content