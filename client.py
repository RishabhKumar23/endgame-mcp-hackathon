# Import necessary libraries
import asyncio
import os
import sys
import json
from typing import Optional
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
from google.genai import types
from google.genai.types import Tool, FunctionDeclaration
from dotenv import load_dotenv
import colorama
from colorama import Fore, Style, Back

# Initialize colorama for cross-platform colored text
colorama.init()

# Define color scheme
COLOR_SERVER = Fore.BLUE + Style.BRIGHT
COLOR_TOOL_CALL = Fore.YELLOW
COLOR_USER = Fore.GREEN
COLOR_RESPONSE = Fore.CYAN
COLOR_ERROR = Fore.RED
COLOR_BORDER = Fore.MAGENTA
RESET = Style.RESET_ALL

# Load environment variables from .env file
load_dotenv()


def print_box(text: str, color: str = Fore.WHITE, title: Optional[str] = None):
    """Print text in a styled box with optional title."""
    lines = text.split("\n")
    max_length = max(len(line) for line in lines)

    if title:
        title_line = f"[ {title} ]"
        lines = [title_line] + ["─" * len(title_line)] + lines
        max_length = max(max_length, len(title_line))

    top = "╭" + "─" * (max_length + 2) + "╮"
    bottom = "╰" + "─" * (max_length + 2) + "╯"
    middle = "\n".join([f"│ {line.ljust(max_length)} │" for line in lines])

    print(color + top)
    print(middle)
    print(bottom + RESET)


class MCPClient:
    def __init__(self):
        """Initialize the MCP client and configure the Gemini API."""
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file")

        self.genai_client = genai.Client(api_key=gemini_api_key)

    async def connect_to_server(self, server_script_path: str):
        """Connect to the MCP server and list available tools."""
        command = "python" if server_script_path.endswith(".py") else "node"
        server_params = StdioServerParameters(
            command=command, args=[server_script_path]
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )

        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )

        await self.session.initialize()
        response = await self.session.list_tools()
        tools = response.tools

        tool_list = ", ".join([tool.name for tool in tools])
        print_box(
            f"Connected successfully!\nAvailable tools: {tool_list}",
            color=COLOR_SERVER,
            title="Server Connection",
        )

    async def process_query(self, query: str) -> str:
        """Process user query through Gemini with tool calling."""
        user_prompt_content = types.Content(
            role="user", parts=[types.Part.from_text(text=query)]
        )

        conversation = [user_prompt_content]
        final_text = []

        while True:
            response = self.genai_client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=conversation,
                config=types.GenerateContentConfig(tools=self.function_declarations),
            )

            function_calls = []
            new_parts = []

            for candidate in response.candidates:
                if not candidate.content.parts:
                    continue

                for part in candidate.content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)
                        new_parts.append(part)
                    elif part.text:
                        final_text.append(part.text)

            if not function_calls:
                break

            for call_part, call in zip(new_parts, function_calls):
                tool_name = call.name
                tool_args = json.dumps(call.args, indent=2)
                print_box(
                    f"Tool: {tool_name}\nArguments:\n{tool_args}",
                    color=COLOR_TOOL_CALL,
                    title="Tool Call",
                )

                try:
                    result = await self.session.call_tool(tool_name, call.args)
                    function_response = {"result": result.content}
                except Exception as e:
                    function_response = {"error": str(e)}

                function_response_part = types.Part.from_function_response(
                    name=tool_name, response=function_response
                )
                tool_response_content = types.Content(
                    role="tool", parts=[function_response_part]
                )

                conversation.append(call_part)
                conversation.append(tool_response_content)

        return "\n".join(final_text).strip()

    async def chat_loop(self):
        """Run an interactive chat session with the user."""
        print_box(
            "Type your queries below ('quit' to exit)",
            COLOR_SERVER,
            "MCP Client Started",
        )

        while True:
            try:
                query = input(f"{COLOR_USER}➤ Query: {RESET}").strip()
                if query.lower() == "quit":
                    break

                response = await self.process_query(query)
                print_box(response, COLOR_RESPONSE, "Response")
            except KeyboardInterrupt:
                print_box("Session terminated by user", COLOR_ERROR, "Warning")
                break

    async def cleanup(self):
        """Clean up resources before exiting."""
        await self.exit_stack.aclose()


def clean_schema(schema):
    """Recursively removes 'title' fields from JSON schema."""
    if isinstance(schema, dict):
        schema.pop("title", None)
        if "properties" in schema:
            for key in schema["properties"]:
                schema["properties"][key] = clean_schema(schema["properties"][key])
    return schema


def convert_mcp_tools_to_gemini(mcp_tools):
    """Converts MCP tools to Gemini-compatible format."""
    gemini_tools = []
    for tool in mcp_tools:
        parameters = clean_schema(tool.inputSchema)
        function_declaration = FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parameters=parameters,
        )
        gemini_tools.append(Tool(function_declarations=[function_declaration]))
    return gemini_tools


async def main():
    """Main function to start the MCP client."""
    if len(sys.argv) < 2:
        print_box(
            "Usage: python client.py <path_to_server_script>", COLOR_ERROR, "Error"
        )
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.chat_loop()
    except Exception as e:
        print_box(str(e), COLOR_ERROR, "Error")
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
