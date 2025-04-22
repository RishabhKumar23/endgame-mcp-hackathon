"""
MCP Client Application

A professional interface for interacting with MCP servers using Google's Gemini AI.
Handles tool discovery, function calling, and maintains a clean separation between:
- Network communication
- AI processing
- User interface
"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import colorama
from colorama import Fore, Style
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.types import FunctionDeclaration, Tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Initialize colorama
colorama.init()

# Load environment variables
load_dotenv()


class MessageType(Enum):
    """Classification for different message categories"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    TOOL_CALL = "TOOL_CALL"
    RESPONSE = "RESPONSE"


@dataclass
class ClientConfig:
    """Configuration parameters for client behavior"""

    gemini_model: str = "gemini-2.0-flash-001"
    max_retries: int = 3
    colors: Dict[MessageType, str] = field(
        default_factory=lambda: {
            MessageType.INFO: Fore.CYAN,
            MessageType.WARNING: Fore.YELLOW,
            MessageType.ERROR: Fore.RED,
            MessageType.TOOL_CALL: Fore.MAGENTA,
            MessageType.RESPONSE: Fore.GREEN,
        }
    )


class DisplayManager:
    """Handles all user-facing output with consistent styling"""

    def __init__(self, config: ClientConfig):
        self.config = config
        self.line_length = 80

    def _format_message(self, text: str, msg_type: MessageType) -> str:
        """Apply consistent formatting based on message type"""
        color = self.config.colors.get(msg_type, Fore.WHITE)
        return f"{color}[{msg_type.value}] {text}{Style.RESET_ALL}"

    def display(self, text: str, msg_type: MessageType = MessageType.INFO):
        """Main display method with smart wrapping"""
        wrapped_text = "\n".join(
            text[i : i + self.line_length]
            for i in range(0, len(text), self.line_length)
        )  # Close the parenthesis here
        print(self._format_message(wrapped_text, msg_type))

    def display_tool_call(self, name: str, args: Dict[str, Any]):
        """Specialized tool call display"""
        header = f"Calling: {name}"
        args_str = json.dumps(args, indent=2)
        content = f"{header}\n{args_str}"
        self.display(content, MessageType.TOOL_CALL)


class MCPClient:
    """Core client handling MCP server communication and AI integration"""

    def __init__(self, config: ClientConfig):
        self.config = config
        self.display = DisplayManager(config)
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

        if not (api_key := os.getenv("GEMINI_API_KEY")):
            raise EnvironmentError("GEMINI_API_KEY not found in environment variables")
        self.ai_client = genai.Client(api_key=api_key)

    async def connect(self, server_script: str):
        """Establish connection to MCP server"""
        try:
            command = "python" if server_script.endswith(".py") else "node"
            params = StdioServerParameters(command=command, args=[server_script])

            async with stdio_client(params) as transport:
                self.session = ClientSession(*transport)
                await self.session.initialize()

                tools = (await self.session.list_tools()).tools
                self.function_declarations = self._convert_tools(tools)

                self.display.display(
                    f"Connected to server with {len(tools)} tools available",
                    MessageType.INFO,
                )

        except Exception as e:
            self.display.display(f"Connection failed: {str(e)}", MessageType.ERROR)
            raise

    def _convert_tools(self, tools: List[Any]) -> List[Tool]:
        """Convert MCP tools to Gemini-compatible format"""
        return [
            Tool(
                function_declarations=[
                    FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters=self._clean_schema(tool.inputSchema),
                    )
                ]
            )
            for tool in tools
        ]

    def _clean_schema(self, schema: Dict) -> Dict:
        """Sanitize JSON schema for AI consumption"""

        def recursive_clean(obj):
            if isinstance(obj, dict):
                obj.pop("title", None)
                return {k: recursive_clean(v) for k, v in obj.items()}
            return obj

        return recursive_clean(schema)

    async def process_query(self, query: str) -> str:
        """Orchestrate the full query processing lifecycle"""
        conversation = [types.Content(role="user", parts=[types.Part.from_text(query)])]

        for attempt in range(self.config.max_retries):
            try:
                response = self.ai_client.models.generate_content(
                    model=self.config.gemini_model,
                    contents=conversation,
                    config=types.GenerateContentConfig(
                        tools=self.function_declarations
                    ),
                )
                return await self._handle_response(response, conversation)
            except Exception as e:
                if attempt == self.config.max_retries - 1:
                    raise
                self.display.display(
                    f"Retrying... ({attempt+1}/{self.config.max_retries})",
                    MessageType.WARNING,
                )

    async def _handle_response(self, response, conversation) -> str:
        """Process AI response and handle tool calls"""
        final_text = []
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.text:
                    final_text.append(part.text)
                elif part.function_call:
                    result = await self._execute_tool_call(
                        part.function_call, conversation
                    )
                    conversation.extend(result)
        return "\n".join(final_text)

    async def _execute_tool_call(self, call, conversation):
        """Execute a single tool call and update conversation"""
        self.display.display_tool_call(call.name, call.args)

        try:
            result = await self.session.call_tool(call.name, call.args)
            response_part = types.Part.from_function_response(
                name=call.name, response={"result": result.content}
            )
            return [
                types.Content(role="function", parts=[call]),
                types.Content(role="tool", parts=[response_part]),
            ]
        except Exception as e:
            self.display.display(f"Tool call failed: {str(e)}", MessageType.ERROR)
            return []


async def main():
    """Application entry point"""
    config = ClientConfig()
    client = MCPClient(config)

    try:
        if len(sys.argv) < 2:
            raise ValueError("Missing server script path")

        await client.connect(sys.argv[1])

        client.display.display(
            "MCP Client ready. Type queries below ('exit' to quit)", MessageType.INFO
        )

        while (query := input(f"{Fore.WHITE}> ").strip().lower()) != "exit":
            if not query:
                continue
            try:
                response = await client.process_query(query)
                client.display.display(response, MessageType.RESPONSE)
            except Exception as e:
                client.display.display(str(e), MessageType.ERROR)

    except KeyboardInterrupt:
        client.display.display("Session terminated", MessageType.INFO)
    finally:
        await client.exit_stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
