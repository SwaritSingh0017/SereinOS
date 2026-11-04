"""
UI Engine - Boot experience and conversational interface for SereinOS.
Provides the primary interface: Serein-first, desktop secondary.
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import threading
import time
import os
import json
from pathlib import Path
from datetime import datetime


class UIMode(Enum):
    """UI interaction modes."""
    BOOT = "boot"
    CONVERSATIONAL = "conversational"
    VISUAL = "visual"  # Desktop/apps visible
    OVERLAY = "overlay"  # Serein overlay on top of desktop
    MINIMAL = "minimal"  # Tray/icon only


@dataclass
class UIConfig:
    """UI configuration."""
    mode: UIMode = UIMode.CONVERSATIONAL
    theme: str = "dark"  # dark, light, auto
    opacity: float = 0.95
    position: str = "center"  # center, bottom, top, floating
    width: int = 800
    height: int = 600
    font_family: str = "Cascadia Code, Consolas, monospace"
    font_size: int = 14
    animations: bool = True
    always_on_top: bool = True
    click_through: bool = False
    hide_desktop_on_boot: bool = True
    auto_hide_delay: int = 5  # seconds of inactivity before auto-hide


class UIBackend(ABC):
    """Abstract UI backend."""
    
    @abstractmethod
    def initialize(self) -> bool:
        pass
    
    @abstractmethod
    def create_window(self, config: UIConfig) -> bool:
        pass
    
    @abstractmethod
    def show(self):
        pass
    
    @abstractmethod
    def hide(self):
        pass
    
    @abstractmethod
    def set_mode(self, mode: UIMode):
        pass
    
    @abstractmethod
    def append_message(self, role: str, content: str, metadata: Dict = None):
        pass
    
    @abstractmethod
    def set_input_placeholder(self, text: str):
        pass
    
    @abstractmethod
    def on_input_submit(self, callback: Callable[[str], None]):
        pass
    
    @abstractmethod
    def run(self):
        pass
    
    @abstractmethod
    def shutdown(self):
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass


class WebViewUI(UIBackend):
    """Web-based UI using WebView (HTML/CSS/JS)."""
    
    def __init__(self):
        self._window = None
        self._input_callback = None
        self._html_path = None
    
    def initialize(self) -> bool:
        try:
            import webview
            self._webview = webview
            return True
        except ImportError:
            try:
                # Try pywebview
                import pywebview as webview
                self._webview = webview
                return True
            except ImportError:
                return False
    
    @property
    def name(self) -> str:
        return "webview"
    
    def create_window(self, config: UIConfig) -> bool:
        if not self._webview:
            return False
        
        # Create HTML interface
        self._html_path = self._create_html_interface(config)
        
        try:
            self._window = self._webview.create_window(
                "SereinOS",
                self._html_path,
                width=config.width,
                height=config.height,
                resizable=True,
                fullscreen=False,
                on_top=config.always_on_top
            )
            
            # Expose Python API to JS
            self._window.expose(self._js_api)
            return True
        except Exception as e:
            print(f"[WebViewUI] Failed to create window: {e}")
            return False
    
    def _create_html_interface(self, config: UIConfig) -> str:
        """Create HTML interface file."""
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SereinOS</title>
    <style>
        :root {{
            --bg-primary: {'#1e1e2e' if config.theme == 'dark' else '#fafafa'};
            --bg-secondary: {'#282a36' if config.theme == 'dark' else '#f0f0f0'};
            --fg-primary: {'#f8f8f2' if config.theme == 'dark' else '#333333'};
            --fg-secondary: {'#6272a4' if config.theme == 'dark' else '#666666'};
            --accent: #bd93f9;
            --accent-hover: #caa9fa;
            --user-msg: #50fa7b;
            --serein-msg: #f8f8f2;
            --font-family: '{config.font_family}';
            --font-size: {config.font_size}px;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: var(--font-family);
            font-size: var(--font-size);
            background: var(--bg-primary);
            color: var(--fg-primary);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        
        #header {{
            padding: 12px 20px;
            background: var(--bg-secondary);
            border-bottom: 1px solid #44475a;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        
        #avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: linear-gradient(135deg, #bd93f9, #ff79c6);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: #1e1e2e;
        }}
        
        #status {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: var(--fg-secondary);
        }}
        
        #status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #50fa7b;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        
        #chat {{
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        
        .message {{
            max-width: 85%;
            animation: fadeIn 0.3s ease;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .message.user {{
            align-self: flex-end;
        }}
        
        .message.serein {{
            align-self: flex-start;
        }}
        
        .message-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
            font-size: 12px;
            color: var(--fg-secondary);
        }}
        
        .message.user .message-header {{
            justify-content: flex-end;
        }}
        
        .message-avatar {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: bold;
        }}
        
        .message.user .message-avatar {{
            background: #50fa7b;
            color: #1e1e2e;
        }}
        
        .message.serein .message-avatar {{
            background: linear-gradient(135deg, #bd93f9, #ff79c6);
            color: #1e1e2e;
        }}
        
        .message-content {{
            padding: 12px 16px;
            border-radius: 16px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        
        .message.user .message-content {{
            background: var(--user-msg);
            color: #1e1e2e;
            border-bottom-right-radius: 4px;
        }}
        
        .message.serein .message-content {{
            background: var(--bg-secondary);
            border: 1px solid #44475a;
            border-bottom-left-radius: 4px;
        }}
        
        .message-time {{
            font-size: 11px;
            color: var(--fg-secondary);
            margin-top: 4px;
            text-align: right;
        }}
        
        .message.user .message-time {{
            text-align: left;
        }}
        
        #input-area {{
            padding: 16px 20px;
            background: var(--bg-secondary);
            border-top: 1px solid #44475a;
        }}
        
        #input-container {{
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }}
        
        #input-wrapper {{
            flex: 1;
            position: relative;
        }}
        
        #message-input {{
            width: 100%;
            min-height: 48px;
            max-height: 150px;
            padding: 12px 16px;
            padding-right: 50px;
            background: var(--bg-primary);
            border: 1px solid #44475a;
            border-radius: 24px;
            color: var(--fg-primary);
            font-family: inherit;
            font-size: inherit;
            resize: none;
            outline: none;
            transition: border-color 0.2s;
        }}
        
        #message-input:focus {{
            border-color: var(--accent);
        }}
        
        #message-input::placeholder {{
            color: var(--fg-secondary);
        }}
        
        #send-btn {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #bd93f9, #ff79c6);
            border: none;
            color: #1e1e2e;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        
        #send-btn:hover {{
            transform: scale(1.05);
            box-shadow: 0 4px 12px rgba(189, 147, 249, 0.4);
        }}
        
        #send-btn:active {{
            transform: scale(0.95);
        }}
        
        #send-btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        
        .typing-indicator {{
            display: flex;
            gap: 4px;
            padding: 8px 16px;
        }}
        
        .typing-indicator span {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent);
            animation: typing 1.4s infinite ease-in-out;
        }}
        
        .typing-indicator span:nth-child(2) {{ animation-delay: 0.2s; }}
        .typing-indicator span:nth-child(3) {{ animation-delay: 0.4s; }}
        
        @keyframes typing {{
            0%, 60%, 100% {{ transform: translateY(0); }}
            30% {{ transform: translateY(-6px); }}
        }}
        
        ::-webkit-scrollbar {{
            width: 8px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: var(--bg-primary);
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: #44475a;
            border-radius: 4px;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: #6272a4;
        }}
    </style>
</head>
<body>
    <div id="header">
        <div>
            <div id="avatar">S</div>
        </div>
        <div id="status">
            <div id="status-dot"></div>
            <span id="status-text">Ready</span>
        </div>
    </div>
    
    <div id="chat"></div>
    
    <div id="input-area">
        <div id="input-container">
            <div id="input-wrapper">
                <textarea 
                    id="message-input" 
                    placeholder="Type a message..." 
                    rows="1"
                    spellcheck="true"
                ></textarea>
            </div>
            <button id="send-btn" title="Send (Enter)">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
            </button>
        </div>
    </div>
    
    <script>
        const chat = document.getElementById('chat');
        const input = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const statusText = document.getElementById('status-text');
        
        let isProcessing = false;
        
        function autoResize() {{
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 150) + 'px';
        }}
        
        input.addEventListener('input', autoResize);
        input.addEventListener('keydown', (e) => {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }});
        
        sendBtn.addEventListener('click', sendMessage);
        
        function sendMessage() {{
            const text = input.value.trim();
            if (!text || isProcessing) return;
            
            isProcessing = true;
            sendBtn.disabled = true;
            input.disabled = true;
            statusText.textContent = 'Thinking...';
            
            appendMessage('user', text);
            input.value = '';
            autoResize();
            
            // Call Python backend
            if (window.pywebview && window.pywebview.api) {{
                window.pywebview.api.on_input(text).then(response => {{
                    if (response && response.content) {{
                        appendMessage('serein', response.content);
                    }}
                    isProcessing = false;
                    sendBtn.disabled = false;
                    input.disabled = false;
                    input.focus();
                    statusText.textContent = 'Ready';
                }}).catch(err => {{
                    console.error(err);
                    appendMessage('serein', 'Sorry, something went wrong.');
                    isProcessing = false;
                    sendBtn.disabled = false;
                    input.disabled = false;
                    input.focus();
                    statusText.textContent = 'Ready';
                }});
            }}
        }}
        
        function appendMessage(role, content) {{
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${{role}}`;
            
            const time = new Date().toLocaleTimeString([], {{hour: '2-digit', minute: '2-digit'}});
            
            msgDiv.innerHTML = `
                <div class="message-header">
                    <div class="message-avatar">${{role === 'user' ? 'You' : 'S'}}</div>
                    <span>${{role === 'user' ? 'You' : 'Serein'}}</span>
                </div>
                <div class="message-content">${{escapeHtml(content)}}</div>
                <div class="message-time">${{time}}</div>
            `;
            
            chat.appendChild(msgDiv);
            chat.scrollTop = chat.scrollHeight;
        }}
        
        function showTyping() {{
            const typingDiv = document.createElement('div');
            typingDiv.className = 'message serein typing-indicator';
            typingDiv.id = 'typing-indicator';
            typingDiv.innerHTML = '<span></span><span></span><span></span>';
            chat.appendChild(typingDiv);
            chat.scrollTop = chat.scrollHeight;
        }}
        
        function hideTyping() {{
            const typing = document.getElementById('typing-indicator');
            if (typing) typing.remove();
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        // Focus input on load
        input.focus();
        
        // Expose to Python
        window.sereinUI = {{
            appendMessage,
            showTyping,
            hideTyping,
            setStatus: (text) => statusText.textContent = text
        }};
    </script>
</body>
</html>
"""
        html_file = Path("assets/serein_ui.html")
        html_file.parent.mkdir(exist_ok=True)
        html_file.write_text(html_content, encoding="utf-8")
        return str(html_file.absolute())
    
    def _js_api(self):
        """JavaScript API exposed to frontend."""
        class JSAPI:
            def __init__(self, ui):
                self.ui = ui
            
            def on_input(self, text: str):
                if self.ui._input_callback:
                    return self.ui._input_callback(text)
                return {"content": "..."}
        
        return JSAPI(self)
    
    def show(self):
        pass  # WebView shows automatically
    
    def hide(self):
        if self._window:
            self._window.hide()
    
    def set_mode(self, mode: UIMode):
        if self._window:
            if mode == UIMode.MINIMAL:
                self._window.hide()
            else:
                self._window.show()
    
    def append_message(self, role: str, content: str, metadata: Dict = None):
        if self._window:
            self._window.evaluate_js(f"window.sereinUI.appendMessage('{role}', `{content.replace('`', '\\`')}`)")
    
    def set_input_placeholder(self, text: str):
        if self._window:
            self._window.evaluate_js(f"document.getElementById('message-input').placeholder = '{text}'")
    
    def on_input_submit(self, callback: Callable[[str], None]):
        self._input_callback = callback
    
    def run(self):
        if self._webview and self._window:
            self._webview.start()
    
    def shutdown(self):
        if self._webview:
            self._webview.destroy_window(self._window)


class ConsoleUI(UIBackend):
    """Console/terminal UI fallback."""
    
    def __init__(self):
        self._input_callback = None
        self._running = False
    
    @property
    def name(self) -> str:
        return "console"
    
    def initialize(self) -> bool:
        return True
    
    def create_window(self, config: UIConfig) -> bool:
        return True
    
    def show(self):
        pass
    
    def hide(self):
        pass
    
    def set_mode(self, mode: UIMode):
        pass
    
    def append_message(self, role: str, content: str, metadata: Dict = None):
        prefix = "You" if role == "user" else "Serein"
        print(f"\n{prefix}: {content}")
    
    def set_input_placeholder(self, text: str):
        pass
    
    def on_input_submit(self, callback: Callable[[str], None]):
        self._input_callback = callback
    
    def run(self):
        self._running = True
        try:
            while self._running:
                try:
                    user_input = input("\nYou: ").strip()
                    if user_input and self._input_callback:
                        self._input_callback(user_input)
                except (EOFError, KeyboardInterrupt):
                    break
        except Exception:
            pass
    
    def shutdown(self):
        self._running = False


class UIEngine:
    """Main UI engine managing the interface."""
    
    def __init__(self, config: UIConfig = None):
        self.config = config or UIConfig()
        self._backend: Optional[UIBackend] = None
        self._mode = self.config.mode
        self._callbacks: Dict[str, List[Callable]] = {
            "on_mode_change": [],
            "on_boot_complete": [],
            "on_shutdown": []
        }
        self._initialized = False
        self._select_backend()
    
    def _select_backend(self):
        """Select best available UI backend."""
        # Try WebView first
        webview = WebViewUI()
        if webview.initialize():
            self._backend = webview
            print(f"[UIEngine] Using {webview.name} backend")
            return
        
        # Fallback to console
        console = ConsoleUI()
        console.initialize()
        self._backend = console
        print(f"[UIEngine] Using {console.name} backend")
    
    def initialize(self) -> bool:
        """Initialize UI."""
        if self._initialized:
            return True
        
        if self._backend and self._backend.create_window(self.config):
            self._initialized = True
            return True
        return False
    
    def boot(self, greeting: str = None):
        """Show boot experience."""
        self._mode = UIMode.BOOT
        self._backend.set_mode(UIMode.BOOT)
        
        if greeting:
            self._backend.append_message("serein", greeting)
        
        self._emit("on_boot_complete")
    
    def start_conversation(self):
        """Switch to conversational mode."""
        self._mode = UIMode.CONVERSATIONAL
        self._backend.set_mode(UIMode.CONVERSATIONAL)
        self._emit("on_mode_change", UIMode.CONVERSATIONAL)
    
    def show(self):
        self._backend.show()
    
    def hide(self):
        self._backend.hide()
    
    def append_message(self, role: str, content: str, metadata: Dict = None):
        self._backend.append_message(role, content, metadata)
    
    def set_input_placeholder(self, text: str):
        self._backend.set_input_placeholder(text)
    
    def on_input(self, callback: Callable[[str], None]):
        self._backend.on_input_submit(callback)
    
    def on(self, event: str, callback: Callable):
        if event in self._callbacks:
            self._callbacks[event].append(callback)
    
    def _emit(self, event: str, *args):
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                print(f"[UIEngine] Callback error: {e}")
    
    def run(self):
        """Run UI event loop."""
        if self._backend:
            self._backend.run()
    
    def shutdown(self):
        self._emit("on_shutdown")
        if self._backend:
            self._backend.shutdown()
        self._initialized = False
    
    def get_mode(self) -> UIMode:
        return self._mode
    
    def set_mode(self, mode: UIMode):
        self._mode = mode
        self._backend.set_mode(mode)
        self._emit("on_mode_change", mode)
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "backend": self._backend.name if self._backend else "none",
            "mode": self._mode.value,
            "initialized": self._initialized
        }