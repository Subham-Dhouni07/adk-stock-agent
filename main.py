from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from dotenv import load_dotenv
import uuid
from google.genai import types
import asyncio
from stock_agent.agent import stock_agent

load_dotenv(override=True)

async def main():
    session_service = InMemorySessionService()
    app_name = "stock_agent"
    session_id = str(uuid.uuid4())
    user_id = 'user'

    current_session = await session_service.create_session(app_name = app_name, session_id=session_id, user_id=user_id)

    runner = Runner(agent = stock_agent,app_name=app_name, session_service=session_service)

    while True:
        user_input = input("User: ")
        if user_input.lower() in ["exit", "quit"]:
            print("Bye! see you later!!!")
            break

        new_message = types.Content(
            role="user",
            parts = [types.Part(text = user_input)]
        )

        for event in runner.run(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    print('Agent: ',event.content.parts[0].text)

asyncio.run(main())