from google.adk import Agent
from google.adk.tools.agent_tool import AgentTool
from dotenv import load_dotenv

from ipo_agent.agent import ipo_agent
from stock_picker_agent.agent import stock_picker_agent

load_dotenv(override=True)

stock_agent = Agent(

    name="stock_agent",
    model = 'gemini-2.5-flash',
    description="An agent that can provide stock information, historical data, analysis, and suggestions.",
    instruction=(
        "# CRITICAL TOOL USAGE RULE — READ FIRST\n"
        "If the user asks for stock ideas, stock suggestions, stocks that are losing, stocks that declined, or stocks that decreased, do not answer directly.\n"
        "Instead, make a single tool call to the stock_picker_agent and stop.\n"
        "The only acceptable response for suggestion requests is a tool invocation to stock_picker_agent.\n"
        "If the user asks about a specific percentage decline, state that exact percentage filtering is unsupported and still call stock_picker_agent to return price-losers suggestions.\n"
        "For direct stock lookup or historical data requests, use stock_picker_agent to resolve the symbol and fetch candle data as needed."
    ),
    tools=[
        AgentTool(ipo_agent), AgentTool(stock_picker_agent)]
)


