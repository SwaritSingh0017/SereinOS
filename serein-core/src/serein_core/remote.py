"""
Remote Client SDK - Enables cross-device communication with SereinOS core service.
Supports phone apps, desktop clients, web dashboards, and CLI tools.
"""
import asyncio
import logging
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any, Callable, AsyncIterator
from abc import ABC, abstractmethod
import websockets
import aiohttp

logger = logging.getLogger("serein.remote")


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


class MessageType(Enum):
    # System
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    
    # Auth
    AUTH_REQUEST = "auth_request"
    AUTH_RESPONSE = "auth_response"
    AUTH_CHALLENGE = "auth_challenge"
    
    # Chat
    CHAT_MESSAGE = "chat_message"
    CHAT_RESPONSE = "chat_response"
    CHAT_HISTORY = "chat_history"
    
    # System control
    COMMAND_EXECUTE = "command_execute"
    COMMAND_RESULT = "command_result"
    COMMAND_LIST = "command_list"
    
    # Events
    EVENT_SUBSCRIBE = "event_subscribe"
    EVENT_UNSUBSCRIBE = "event_unsubscribe"
    EVENT_NOTIFICATION = "event_notification"
    
    # Status
    STATUS_REQUEST = "status_request"
    STATUS_RESPONSE = "status_response"
    
    # Voice
    VOICE_INPUT = "voice_input"
    VOICE_OUTPUT = "voice_output"
    WAKE_WORD = "wake_word"
    
    # Vision
    VISION_SCREEN = "vision_screen"
    VISION_CAMERA = "vision_camera"
    VISION_OCR = "vision_ocr"
    VISION_DETECTION = "vision_detection"
    
    # Proactive
    PROACTIVE_TRIGGER = "proactive_trigger"
    PROACTIVE_ACTION = "proactive_action"
    
    # Agent
    AGENT_TASK = "agent_task"
    AGENT_STATUS = "agent_status"
    
    # Config
    CONFIG_GET = "config_get"
    CONFIG_SET = "config_set"


@dataclass
class RemoteMessage:
    """Standard message format for remote communication."""
    type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: Optional[str] = None
    
    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "payload": self.payload,
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
        })
    
    @classmethod
    def from_json(cls, data: str) -> "RemoteMessage":
        obj = json.loads(data)
        return cls(
            type=MessageType(obj["type"]),
            payload=obj.get("payload", {}),
            request_id=obj.get("request_id", str(uuid.uuid4())),
            timestamp=datetime.fromisoformat(obj["timestamp"]) if "timestamp" in obj else datetime.now(),
            correlation_id=obj.get("correlation_id"),
        )


class RemoteClient(ABC):
    """Abstract base class for remote clients."""
    
    @abstractmethod
    async def connect(self, uri: str, auth_token: str = None) -> bool:
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        pass
    
    @abstractmethod
    async def send(self, message: RemoteMessage) -> None:
        pass
    
    @abstractmethod
    async def request(self, message: RemoteMessage, timeout: float = 30.0) -> Optional[RemoteMessage]:
        pass
    
    @property
    @abstractmethod
    def state(self) -> ConnectionState:
        pass
    
    @abstractmethod
    def on_message(self, handler: Callable[[RemoteMessage], Any]) -> None:
        pass
    
    @abstractmethod
    def on_state_change(self, handler: Callable[[ConnectionState], Any]) -> None:
        pass


class WebSocketClient(RemoteClient):
    """WebSocket-based remote client for real-time communication."""
    
    def __init__(self, auto_reconnect: bool = True, reconnect_interval: float = 5.0):
        self._uri: Optional[str] = None
        self._auth_token: Optional[str] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._state = ConnectionState.DISCONNECTED
        self._auto_reconnect = auto_reconnect
        self._reconnect_interval = reconnect_interval
        self._message_handlers: List[Callable[[RemoteMessage], Any]] = []
        self._state_handlers: List[Callable[[ConnectionState], Any]] = []
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._receive_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
    
    @property
    def state(self) -> ConnectionState:
        return self._state
    
    def _set_state(self, new_state: ConnectionState) -> None:
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            logger.info(f"Connection state: {old_state.value} → {new_state.value}")
            for handler in self._state_handlers:
                try:
                    handler(new_state)
                except Exception as e:
                    logger.error(f"State handler error: {e}")
    
    async def connect(self, uri: str, auth_token: str = None) -> bool:
        """Connect to remote server."""
        self._uri = uri
        self._auth_token = auth_token
        
        try:
            self._set_state(ConnectionState.CONNECTING)
            
            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            
            self._ws = await websockets.connect(uri, extra_headers=headers)
            
            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            # Send auth if needed
            if auth_token:
                auth_msg = RemoteMessage(
                    type=MessageType.AUTH_REQUEST,
                    payload={"token": auth_token}
                )
                response = await self.request(auth_msg, timeout=10.0)
                if response and response.type == MessageType.AUTH_RESPONSE:
                    if response.payload.get("success"):
                        self._set_state(ConnectionState.AUTHENTICATED)
                    else:
                        self._set_state(ConnectionState.ERROR)
                        return False
                else:
                    self._set_state(ConnectionState.ERROR)
                    return False
            else:
                self._set_state(ConnectionState.CONNECTED)
            
            # Start ping task
            self._ping_task = asyncio.create_task(self._ping_loop())
            
            logger.info(f"Connected to {uri}")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._set_state(ConnectionState.ERROR)
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from server."""
        self._auto_reconnect = False
        
        if self._ping_task:
            self._ping_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._set_state(ConnectionState.DISCONNECTED)
        logger.info("Disconnected")
    
    async def send(self, message: RemoteMessage) -> None:
        """Send message."""
        if not self._ws or self._state == ConnectionState.DISCONNECTED:
            raise RuntimeError("Not connected")
        
        await self._ws.send(message.to_json())
    
    async def request(self, message: RemoteMessage, timeout: float = 30.0) -> Optional[RemoteMessage]:
        """Send request and wait for response."""
        if not self._ws:
            raise RuntimeError("Not connected")
        
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[message.request_id] = future
        
        await self.send(message)
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_requests.pop(message.request_id, None)
            raise TimeoutError(f"Request {message.request_id} timed out")
        except Exception:
            self._pending_requests.pop(message.request_id, None)
            raise
    
    async def _receive_loop(self) -> None:
        """Main receive loop."""
        try:
            async for raw_message in self._ws:
                try:
                    message = RemoteMessage.from_json(raw_message)
                    
                    # Handle response to pending request
                    if message.correlation_id and message.correlation_id in self._pending_requests:
                        future = self._pending_requests.pop(message.correlation_id)
                        future.set_result(message)
                        continue
                    
                    # Handle incoming messages
                    for handler in self._message_handlers:
                        try:
                            await handler(message)
                        except Exception as e:
                            logger.error(f"Message handler error: {e}")
                            
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received: {raw_message[:100]}")
                except Exception as e:
                    logger.error(f"Message processing error: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
        finally:
            self._set_state(ConnectionState.DISCONNECTED)
            if self._auto_reconnect:
                await self._reconnect()
    
    async def _ping_loop(self) -> None:
        """Send periodic pings."""
        while self._ws and self._state != ConnectionState.DISCONNECTED:
            try:
                await asyncio.sleep(30)
                if self._ws:
                    ping = RemoteMessage(type=MessageType.PING, payload={"timestamp": datetime.now().isoformat()})
                    await self.send(ping)
            except Exception as e:
                logger.error(f"Ping error: {e}")
    
    async def _reconnect(self) -> None:
        """Attempt reconnection."""
        if not self._auto_reconnect or not self._uri:
            return
        
        self._set_state(ConnectionState.CONNECTING)
        logger.info(f"Reconnecting in {self._reconnect_interval}s...")
        
        await asyncio.sleep(self._reconnect_interval)
        
        retry_count = 0
        max_retries = 10
        
        while self._auto_reconnect and retry_count < max_retries:
            try:
                success = await self.connect(self._uri, self._auth_token)
                if success:
                    logger.info("Reconnected successfully")
                    return
            except Exception as e:
                logger.error(f"Reconnect attempt {retry_count + 1} failed: {e}")
            
            retry_count += 1
            await asyncio.sleep(self._reconnect_interval * (1.5 ** retry_count))
        
        logger.error("Max reconnection attempts reached")
        self._set_state(ConnectionState.ERROR)
    
    def on_message(self, handler: Callable[[RemoteMessage], Any]) -> None:
        """Register message handler."""
        self._message_handlers.append(handler)
    
    def on_state_change(self, handler: Callable[[ConnectionState], Any]) -> None:
        """Register state change handler."""
        self._state_handlers.append(handler)
    
    # Convenience methods
    
    async def chat(self, message: str, context: Dict = None) -> Optional[str]:
        """Send chat message."""
        req = RemoteMessage(
            type=MessageType.CHAT_MESSAGE,
            payload={"message": message, "context": context or {}}
        )
        resp = await self.request(req)
        if resp and resp.type == MessageType.CHAT_RESPONSE:
            return resp.payload.get("content")
        return None
    
    async def execute_command(self, command: str, params: Dict = None, 
                              confirmation: bool = False) -> Optional[Dict]:
        """Execute system command."""
        req = RemoteMessage(
            type=MessageType.COMMAND_EXECUTE,
            payload={"command": command, "params": params or {}, "confirmation_token": ""} if confirmation else
                     {"command": command, "params": params or {}}
        )
        resp = await self.request(req)
        if resp and resp.type == MessageType.COMMAND_RESULT:
            return resp.payload
        return None
    
    async def get_status(self) -> Optional[Dict]:
        """Get system status."""
        req = RemoteMessage(type=MessageType.STATUS_REQUEST)
        resp = await self.request(req)
        if resp and resp.type == MessageType.STATUS_RESPONSE:
            return resp.payload
        return None
    
    async def subscribe_events(self, event_types: List[str]) -> bool:
        """Subscribe to event types."""
        req = RemoteMessage(
            type=MessageType.EVENT_SUBSCRIBE,
            payload={"events": event_types}
        )
        resp = await self.request(req)
        return resp and resp.type == MessageType.EVENT_SUBSCRIBE and resp.payload.get("success", False)
    
    async def send_voice(self, audio_data: bytes, format: str = "wav") -> Optional[str]:
        """Send voice input."""
        import base64
        req = RemoteMessage(
            type=MessageType.VOICE_INPUT,
            payload={
                "audio": base64.b64encode(audio_data).decode(),
                "format": format,
            }
        )
        resp = await self.request(req)
        if resp and resp.type == MessageType.VOICE_OUTPUT:
            return resp.payload.get("transcript")
        return None
    
    async def capture_screen(self, region: Dict = None) -> Optional[Dict]:
        """Capture screen."""
        req = RemoteMessage(
            type=MessageType.VISION_SCREEN,
            payload={"region": region}
        )
        resp = await self.request(req)
        if resp and resp.type == MessageType.VISION_SCREEN:
            return resp.payload
        return None


class HTTPClient:
    """HTTP-based client for REST API access."""
    
    def __init__(self, base_url: str, auth_token: str = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
            self._session = aiohttp.ClientSession(timeout=self.timeout, headers=headers)
        return self._session
    
    async def _request(self, method: str, path: str, **kwargs) -> Dict:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        async with session.request(method, url, **kwargs) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def get(self, path: str, params: Dict = None) -> Dict:
        return await self._request("GET", path, params=params)
    
    async def post(self, path: str, data: Dict = None, json: Dict = None) -> Dict:
        return await self._request("POST", path, data=data, json=json)
    
    async def put(self, path: str, json: Dict = None) -> Dict:
        return await self._request("PUT", path, json=json)
    
    async def delete(self, path: str) -> Dict:
        return await self._request("DELETE", path)
    
    # Convenience methods
    
    async def chat(self, message: str, context: Dict = None) -> Dict:
        return await self.post("/api/chat", json={"message": message, "context": context or {}})
    
    async def execute_command(self, command: str, params: Dict = None, 
                              confirmation_token: str = None) -> Dict:
        payload = {"command": command, "params": params or {}}
        if confirmation_token:
            payload["confirmation_token"] = confirmation_token
        return await self.post("/api/command", json=payload)
    
    async def get_status(self) -> Dict:
        return await self.get("/api/status")
    
    async def get_config(self) -> Dict:
        return await self.get("/api/config")
    
    async def update_config(self, config: Dict) -> Dict:
        return await self.put("/api/config", json=config)
    
    async def list_commands(self) -> Dict:
        return await self.get("/api/commands")
    
    async def get_events(self, limit: int = 100) -> Dict:
        return await self.get("/api/events", params={"limit": limit})
    
    async def submit_task(self, task_data: Dict) -> Dict:
        return await self.post("/api/agent/tasks", json=task_data)
    
    async def get_task_status(self, task_id: str) -> Dict:
        return await self.get(f"/api/agent/tasks/{task_id}")
    
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# Global factory
async def create_client(uri: str = None, auth_token: str = None, 
                       client_type: str = "websocket") -> RemoteClient:
    """Factory to create appropriate client."""
    if client_type == "websocket":
        client = WebSocketClient()
        await client.connect(uri or "ws://localhost:8080/ws", auth_token)
        return client
    elif client_type == "http":
        base_url = uri.replace("ws://", "http://").replace("wss://", "https://").replace("/ws", "")
        return HTTPClient(base_url, auth_token)
    else:
        raise ValueError(f"Unknown client type: {client_type}")


# Phone app simulation client
class PhoneAppClient:
    """Simulated phone app client with persistent connection and notifications."""
    
    def __init__(self, server_uri: str, device_id: str):
        self.server_uri = server_uri
        self.device_id = device_id
        self.client: Optional[WebSocketClient] = None
        self._notifications: asyncio.Queue = asyncio.Queue()
        self._running = False
    
    async def start(self, auth_token: str = None) -> bool:
        self.client = WebSocketClient(auto_reconnect=True)
        
        # Handle incoming events as notifications
        async def on_message(msg: RemoteMessage):
            if msg.type == MessageType.EVENT_NOTIFICATION:
                await self._notifications.put(msg.payload)
        
        self.client.on_message(on_message)
        
        # Track connection state
        def on_state(state: ConnectionState):
            logger.info(f"Phone app connection: {state.value}")
        
        self.client.on_state_change(on_state)
        
        success = await self.client.connect(self.server_uri, auth_token)
        if success:
            self._running = True
            # Subscribe to all events
            await self.client.subscribe_events([
                "proactive", "voice", "vision", "agent", "system", "chat"
            ])
        
        return success
    
    async def stop(self):
        self._running = False
        if self.client:
            await self.client.disconnect()
    
    async def send_notification(self, title: str, body: str, 
                               notification_type: str = "info") -> None:
        """Send notification to phone (simulated)."""
        # In real app, this would push to FCM/APNs
        logger.info(f"📱 NOTIFICATION: [{notification_type}] {title} - {body}")
    
    async def get_next_notification(self, timeout: float = 30.0) -> Optional[Dict]:
        """Get next notification from queue."""
        try:
            return await asyncio.wait_for(self._notifications.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


# Demo/test function
async def demo_remote_client():
    """Demo using remote client."""
    client = await create_client("ws://localhost:8080/ws", "your-auth-token")
    
    # Chat
    response = await client.chat("Hello from remote client!")
    print(f"Serein: {response}")
    
    # Execute command
    result = await client.execute_command("get_system_status")
    print(f"System status: {result}")
    
    # Get status
    status = await client.get_status()
    print(f"Status: {status}")
    
    await client.disconnect()