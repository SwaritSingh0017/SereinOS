// SereinOS v2.1 Native Desktop Engine
document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const taskbarClockEl = document.getElementById('taskbar-clock');
  const dialogueFeed = document.getElementById('dialogue-feed');
  const userInput = document.getElementById('user-input');
  const sendBtn = document.getElementById('btn-send');
  const micBtn = document.getElementById('btn-mic');
  const statusBubble = document.getElementById('avatar-status-bubble');
  const canvas = document.getElementById('waveform-canvas');
  const ctx = canvas.getContext('2d');

  // Start Menu & Modals
  const startBtn = document.getElementById('btn-start');
  const startMenu = document.getElementById('start-menu');
  const modalWifi = document.getElementById('modal-wifi');
  const wifiIndicator = document.getElementById('wifi-indicator');
  const wifiList = document.getElementById('wifi-networks');
  const wifiConnectBox = document.getElementById('wifi-connect-box');
  const selectedSsidEl = document.getElementById('selected-ssid');
  const wifiPassword = document.getElementById('wifi-password');
  const wifiSpinner = document.getElementById('wifi-spinner');

  let selectedSsid = '';
  let isSpeaking = false;
  let isListening = false;
  let audioContext = null;
  let analyserNode = null;
  let micStream = null;
  let micDataArray = null;
  let speechAnalyserNode = null;
  let speechDataArray = null;
  let currentAudioSource = null;
  let visemeTimeouts = [];
  let mediaRecorder = null;
  let recordedAudioChunks = [];
  let masterGainNode = null;
  let currentVolume = 75;
  let isMuted = false;
  let currentBrightness = 75;

  function getAudioContext() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        audioContext = new AudioCtx();
        masterGainNode = audioContext.createGain();
        const effectiveGain = isMuted ? 0 : Math.max(0, Math.min(1, currentVolume / 100));
        masterGainNode.gain.setValueAtTime(effectiveGain, audioContext.currentTime);
        masterGainNode.connect(audioContext.destination);
      }
    }
    if (audioContext && audioContext.state === 'suspended') {
      audioContext.resume().catch(() => {});
    }
    return audioContext;
  }

  // Unlock AudioContext on any user interaction to satisfy browser autoplay policy
  ['click', 'keydown', 'touchstart'].forEach(evt => {
    document.addEventListener(evt, () => {
      if (audioContext && audioContext.state === 'suspended') {
        audioContext.resume().catch(() => {});
      }
    }, { passive: true });
  });

  function playAudioFeedbackChime() {
    try {
      const ctx = getAudioContext();
      if (!ctx || isMuted || currentVolume === 0) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(587.33, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.08);
      const vol = Math.min(0.2, (currentVolume / 100) * 0.15);
      gain.gain.setValueAtTime(vol, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.13);
    } catch (e) {}
  }

  // 1. Initialize Serein 3D VRM Avatar Stage
  let avatarStage = null;
  if (typeof window.SereinAvatarStage !== 'undefined') {
    try {
      avatarStage = new window.SereinAvatarStage({
        container: document.getElementById('avatar-stage'),
        vrmUrl: '/models/serein.vrm'
      });
      window.sereinAvatar = avatarStage;
    } catch (e) {
      console.warn('VRM Avatar initialization note:', e);
    }
  }

  // Pre-load voices for speech synthesis
  if ('speechSynthesis' in window) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }

  // 2. Real-Time Dynamic Clock (Format: Tue, Oct 26 | 19:33)
  function updateClock() {
    const now = new Date();
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const day = days[now.getDay()];
    const month = months[now.getMonth()];
    const date = now.getDate();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    if (taskbarClockEl) taskbarClockEl.textContent = `${day}, ${month} ${date} | ${hours}:${minutes}`;

    const clockDisplay = document.getElementById('clock-display');
    const dateDisplay = document.getElementById('date-display');
    if (clockDisplay) clockDisplay.textContent = `${hours}:${minutes}`;
    if (dateDisplay) dateDisplay.textContent = `${day}, ${month} ${date}`;

    const greetingTime = document.getElementById('greeting-time');
    if (greetingTime) {
      greetingTime.textContent = `[${hours}:${minutes}]`;
    }
  }
  updateClock();
  setInterval(updateClock, 1000);

  // 3. System Telemetry Poll (/api/system/stats)
  async function updateTelemetry() {
    try {
      const res = await fetch('/api/system/stats');
      if (res.ok) {
        const data = await res.json();
        document.getElementById('telemetry-cpu').textContent = `${data.cpu_percent}%`;
        document.getElementById('telemetry-ram').textContent = `${data.ram_used_gb}GB/${data.ram_total_gb}GB`;
        const battFill = document.getElementById('battery-fill');
        if (battFill) battFill.style.width = `${Math.min(100, data.battery_percent)}%`;
      }
    } catch (e) {
      // Fallback display values
      document.getElementById('telemetry-cpu').textContent = `${Math.floor(10 + Math.random() * 4)}%`;
      document.getElementById('telemetry-ram').textContent = `6.8GB/32GB`;
    }
  }
  updateTelemetry();
  setInterval(updateTelemetry, 4000);

  // 4. Vocal Activity Mirrored Waveform (Purple -> Mint Cyan Gradient)
  const numBars = 52;
  const barWidth = 4;
  const barGap = 4;
  let wavePhase = 0;

  function drawWaveform() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const centerY = canvas.height / 2;
    const totalWaveWidth = numBars * (barWidth + barGap);
    const startX = Math.max(10, (canvas.width - totalWaveWidth) / 2);

    let spectrumData = null;
    if (speechAnalyserNode && isSpeaking && speechDataArray) {
      speechAnalyserNode.getByteFrequencyData(speechDataArray);
      spectrumData = speechDataArray;
    } else if (analyserNode && isListening && micDataArray) {
      analyserNode.getByteFrequencyData(micDataArray);
      spectrumData = micDataArray;
    }

    for (let i = 0; i < numBars; i++) {
      const x = startX + i * (barWidth + barGap);
      const distFromCenter = Math.abs(i - numBars / 2) / (numBars / 2);
      const envelope = Math.cos(distFromCenter * (Math.PI / 2)); // Taper at edges

      let halfHeight = 2;

      if (spectrumData && (isSpeaking || isListening)) {
        const binIndex = Math.floor((i / numBars) * (spectrumData.length / 2));
        const val = spectrumData[binIndex] / 255.0;
        halfHeight = Math.max(2.5, val * 26 * envelope);
      } else if (isSpeaking) {
        const wave = Math.sin(wavePhase * 1.5 + i * 0.3) * 16 + Math.cos(wavePhase * 2.5 + i * 0.15) * 8;
        halfHeight = Math.max(3, Math.abs(wave) * envelope);
      } else if (isListening) {
        const wave = Math.sin(wavePhase * 3.0 + i * 0.4) * 12;
        halfHeight = Math.max(3, Math.abs(wave) * envelope);
      } else {
        // Idle ambient breathing wave
        const wave = Math.sin(wavePhase * 0.8 + i * 0.25) * 4;
        halfHeight = Math.max(1.5, (2 + Math.abs(wave)) * envelope);
      }

      // Dual-Color Gradient: Purple on left, Mint/Cyan on right
      const grad = ctx.createLinearGradient(0, centerY - halfHeight, 0, centerY + halfHeight);
      if (i < numBars / 2) {
        grad.addColorStop(0, '#d500f9');
        grad.addColorStop(0.5, '#b388ff');
        grad.addColorStop(1, '#7c4dff');
      } else {
        grad.addColorStop(0, '#00e5ff');
        grad.addColorStop(0.5, '#20e2a3');
        grad.addColorStop(1, '#00b0ff');
      }

      ctx.fillStyle = grad;
      ctx.beginPath();
      // Mirrored pill bars across centerY
      ctx.roundRect(x, centerY - halfHeight, barWidth, halfHeight * 2, 2);
      ctx.fill();
    }

    wavePhase += isSpeaking ? 0.22 : 0.06;
    requestAnimationFrame(drawWaveform);
  }
  drawWaveform();

  // 5. Append Dialogue Message Row
  function appendDialogue(sender, text) {
    const row = document.createElement('div');
    row.className = `dialogue-row ${sender.toLowerCase()}`;

    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');

    row.innerHTML = `
      <span class="dialogue-text">${text}</span>
      <span class="dialogue-time">[${hours}:${minutes}]</span>
    `;

    dialogueFeed.appendChild(row);
    dialogueFeed.scrollTop = dialogueFeed.scrollHeight;
  }

  // 6. Speak Serein Response (Offline Local TTS + HTML5 / Web Audio Hybrid + Avatar Visemes)
  async function speakResponse(text) {
    const cleanText = text.replace(/<[^>]*>?/gm, '');
    if (!cleanText.trim()) return;

    // Clear any previous viseme schedule & active audio playback
    visemeTimeouts.forEach(t => clearTimeout(t));
    visemeTimeouts = [];

    if (currentAudioSource) {
      try { currentAudioSource.stop(); } catch (e) {}
      currentAudioSource = null;
    }

    if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Synthesizing voice...';
    if (avatarStage) avatarStage.setEmotion('happy');

    try {
      // 1. Fetch offline synthesized audio & genuine visemes from backend API
      const res = await fetch('/api/voice/synthesize', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: cleanText })
      });

      if (!res.ok) throw new Error(`Synthesis API returned ${res.status}`);

      const data = await res.json();
      if (!data.audio_base64) throw new Error('No audio data in response');

      // Calculate effective volume (prevent 0% silent boot)
      const effectiveVol = isMuted ? 0 : Math.max(0.2, Math.min(1, (currentVolume || 80) / 100));

      // 2. Primary audio playback via HTML5 Audio element for maximum driver compatibility (VMware ALSA / PulseAudio)
      const audioUrl = 'data:audio/wav;base64,' + data.audio_base64;
      const htmlAudio = new Audio(audioUrl);
      htmlAudio.volume = effectiveVol;

      // 3. Connect to Web Audio Analyser if AudioContext is available & running for FFT
      try {
        const ctx = getAudioContext();
        if (ctx) {
          if (ctx.state === 'suspended') {
            ctx.resume().catch(() => {});
          }
          const binaryString = window.atob(data.audio_base64);
          const len = binaryString.length;
          const bytes = new Uint8Array(len);
          for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
          }
          const audioBuffer = await ctx.decodeAudioData(bytes.buffer.slice(0));
          const source = ctx.createBufferSource();
          source.buffer = audioBuffer;
          currentAudioSource = source;

          if (!speechAnalyserNode) {
            speechAnalyserNode = ctx.createAnalyser();
            speechAnalyserNode.fftSize = 128;
            speechDataArray = new Uint8Array(speechAnalyserNode.frequencyBinCount);
          }
          source.connect(speechAnalyserNode);
          speechAnalyserNode.connect(ctx.destination);

          source.onended = () => {
            clearTimeout(synthWatchdog);
            isSpeaking = false;
            currentAudioSource = null;
            const avatarWrap = document.querySelector('.avatar-wrapper');
            if (avatarWrap) avatarWrap.classList.remove('speaking');
            if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
            if (avatarStage) {
              avatarStage.setEmotion('neutral');
              avatarStage.setViseme('aa', 0);
              avatarStage.stopSpeaking();
            }
          };
          source.start(0);
        }
      } catch (webaudioErr) {
        console.debug('Web Audio FFT connection note:', webaudioErr);
      }

      isSpeaking = true;
      const avatarWrap = document.querySelector('.avatar-wrapper');
      if (avatarWrap) avatarWrap.classList.add('speaking');
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Speaking...';
      if (avatarStage) avatarStage.setEmotion('happy');

      // Watchdog timeout: reset status bubble if playback ends or stalls
      const synthWatchdog = setTimeout(() => {
        if (statusBubble && statusBubble.querySelector('.bubble-text').textContent !== 'Ready and listening.') {
          statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
          const wrap = document.querySelector('.avatar-wrapper');
          if (wrap) wrap.classList.remove('speaking');
          if (avatarStage) avatarStage.setEmotion('neutral');
        }
      }, Math.max(5000, (data.duration_ms || 2500) + 2000));

      // 4. Schedule genuine avatar mouth visemes synchronized with playback
      const visemes = data.visemes || [];
      if (avatarStage && visemes.length > 0) {
        visemes.forEach((v) => {
          const timeoutId = setTimeout(() => {
            if (isSpeaking && avatarStage) {
              const shape = v.viseme || 'aa';
              avatarStage.setViseme(shape, 0.85);
            }
          }, Math.max(0, v.time_ms));
          visemeTimeouts.push(timeoutId);

          const resetId = setTimeout(() => {
            if (isSpeaking && avatarStage) {
              avatarStage.setViseme(v.viseme || 'aa', 0.15);
            }
          }, Math.max(0, v.time_ms + (v.duration_ms || 60)));
          visemeTimeouts.push(resetId);
        });
      } else if (avatarStage) {
        avatarStage.speak(data.duration_ms || Math.max(2200, cleanText.length * 68));
      }

      htmlAudio.onended = () => {
        clearTimeout(synthWatchdog);
        isSpeaking = false;
        currentAudioSource = null;
        const avatarWrap = document.querySelector('.avatar-wrapper');
        if (avatarWrap) avatarWrap.classList.remove('speaking');
        if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
        if (avatarStage) {
          avatarStage.setEmotion('neutral');
          avatarStage.setViseme('aa', 0);
          avatarStage.stopSpeaking();
        }
      };

      htmlAudio.onerror = (e) => {
        console.warn('HTML5 Audio playback note, falling back:', e);
        clearTimeout(synthWatchdog);
        isSpeaking = false;
        if (avatarWrap) avatarWrap.classList.remove('speaking');
        if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
        fallbackBrowserSpeech(cleanText);
      };

      try {
        await htmlAudio.play();
      } catch (playErr) {
        console.warn('HTML5 Audio play note:', playErr);
        fallbackBrowserSpeech(cleanText);
      }

    } catch (err) {
      console.warn('Backend voice synthesize note:', err);
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
      if (avatarStage) avatarStage.setEmotion('neutral');
      const avatarWrap = document.querySelector('.avatar-wrapper');
      if (avatarWrap) avatarWrap.classList.remove('speaking');
      fallbackBrowserSpeech(cleanText);
    }
  }

  function fallbackBrowserSpeech(cleanText) {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.rate = 1.05;
    utterance.pitch = 1.12;

    const voices = window.speechSynthesis.getVoices();
    const sereinVoice = voices.find(v => v.lang.startsWith('en') && (v.name.includes('Natural') || v.name.includes('Female') || v.name.includes('Google') || v.name.includes('Samantha')));
    if (sereinVoice) utterance.voice = sereinVoice;

    utterance.onstart = () => {
      isSpeaking = true;
      const avatarWrap = document.querySelector('.avatar-wrapper');
      if (avatarWrap) avatarWrap.classList.add('speaking');
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Speaking...';
      if (avatarStage) {
        avatarStage.setEmotion('happy');
        avatarStage.speak(Math.max(2200, cleanText.length * 68));
      }
    };

    utterance.onend = () => {
      isSpeaking = false;
      const avatarWrap = document.querySelector('.avatar-wrapper');
      if (avatarWrap) avatarWrap.classList.remove('speaking');
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
      if (avatarStage) {
        avatarStage.setEmotion('neutral');
        avatarStage.stopSpeaking();
      }
    };

    utterance.onerror = () => {
      isSpeaking = false;
      if (avatarStage) {
        avatarStage.setEmotion('neutral');
        avatarStage.stopSpeaking();
      }
    };

    window.speechSynthesis.speak(utterance);
  }

  // 7. Submit User Query to Serein Core
  async function submitQuery(text) {
    const query = (text || userInput.value).trim();
    if (!query) return;

    userInput.value = '';
    appendDialogue('User', query);

    if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Thinking...';
    if (avatarStage) avatarStage.setEmotion('thinking');

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: query})
      });

      if (res.ok) {
        const data = await res.json();
        appendDialogue('Serein', data.content);
        speakResponse(data.content);
      } else {
        const fallback = `I'm processing your request "${query}". Neural core responded with status ${res.status}.`;
        appendDialogue('Serein', fallback);
        speakResponse(fallback);
      }
    } catch (err) {
      // Local fallback intelligence if network fetch drops
      let reply = "I hear you, Kai. All core operating system systems are running smoothly.";
      const lower = query.toLowerCase();
      if (lower.includes('schedule')) {
        reply = "I've reviewed your schedule: your afternoon deep-work session is secured and tasks are prioritized.";
      } else if (lower.includes('project') || lower.includes('review')) {
        reply = "Let's review the upcoming projects: Stage 2 Living Avatar and UI architecture are verified.";
      } else if (lower.includes('briefing')) {
        reply = "Daily Briefing: CPU load is nominal, 2 background daemons active, and your environment is secure.";
      }
      appendDialogue('Serein', reply);
      speakResponse(reply);
    }
  }

  // 8. Voice Microphone Input Handling (Offline Local STT via /api/voice/transcribe)
  async function toggleListening() {
    if (isListening) {
      await stopListening();
    } else {
      await startListening();
    }
  }

  let recognition = null;
  if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    try {
      recognition = new SpeechRec();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = 'en-US';

      recognition.onresult = (e) => {
        if (e.results && e.results[0] && e.results[0][0]) {
          const transcript = e.results[0][0].transcript;
          if (transcript) submitQuery(transcript);
        }
      };
      recognition.onerror = () => {};
    } catch (e) {}
  }

  async function startListening() {
    isListening = true;
    recordedAudioChunks = [];
    micBtn.classList.add('active');
    if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Listening to you...';
    if (avatarStage) avatarStage.setEmotion('listening');

    // Hook Web Audio Analyser for live microphone waveform visualizer
    try {
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const ctx = getAudioContext();
        if (ctx) {
          const source = ctx.createMediaStreamSource(micStream);
          analyserNode = ctx.createAnalyser();
          analyserNode.fftSize = 128;
          micDataArray = new Uint8Array(analyserNode.frequencyBinCount);
          source.connect(analyserNode);
        }

        // Capture raw audio chunks using MediaRecorder for offline backend transcription
        if (typeof MediaRecorder !== 'undefined') {
          mediaRecorder = new MediaRecorder(micStream);
          mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
              recordedAudioChunks.push(event.data);
            }
          };
          mediaRecorder.start(100);
        }
      } else {
        throw new Error('Microphone audio input not supported in this environment');
      }
    } catch (e) {
      console.warn('Mic stream access error:', e);
      isListening = false;
      micBtn.classList.remove('active');
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
      if (avatarStage) avatarStage.setEmotion('neutral');
      showToast('Microphone not detected. In VMware, connect Sound Card in VM settings or type in chat.', '🎤');
      return;
    }

    if (recognition) {
      try { recognition.start(); } catch (e) {}
    }
  }

  async function stopListening() {
    isListening = false;
    micBtn.classList.remove('active');
    if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Transcribing speech...';

    if (recognition) {
      try { recognition.stop(); } catch (e) {}
    }

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      try { mediaRecorder.stop(); } catch (e) {}
      await new Promise(r => setTimeout(r, 120));
    }

    if (micStream) {
      micStream.getTracks().forEach(t => t.stop());
      micStream = null;
    }

    // Stream recorded audio to /api/voice/transcribe
    if (recordedAudioChunks.length > 0) {
      try {
        const audioBlob = new Blob(recordedAudioChunks, { type: 'audio/wav' });
        const reader = new FileReader();
        reader.onloadend = async () => {
          try {
            const base64Audio = (reader.result || '').split(',')[1] || '';
            if (base64Audio) {
              const res = await fetch('/api/voice/transcribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audio_base64: base64Audio })
              });
              if (res.ok) {
                const data = await res.json();
                if (data.text && data.text.trim()) {
                  submitQuery(data.text);
                  return;
                }
              }
            }
          } catch (err) {
            console.warn('Offline transcription request error:', err);
          } finally {
            if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
          }
        };
        reader.readAsDataURL(audioBlob);
      } catch (err) {
        console.warn('Error packing audio chunks:', err);
        if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
      }
    } else {
      if (statusBubble) statusBubble.querySelector('.bubble-text').textContent = 'Ready and listening.';
    }
  }

  // 9. Input & Button Listeners
  sendBtn.addEventListener('click', () => submitQuery());
  micBtn.addEventListener('click', toggleListening);
  userInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') submitQuery();
  });

  // Shortcuts Grid within OS Widget
  document.getElementById('sc-briefing').addEventListener('click', () => {
    submitQuery("Daily Briefing");
  });
  document.getElementById('sc-chat').addEventListener('click', () => {
    userInput.focus();
    appendDialogue('Serein', "Neural chat active. Ask me anything or click the microphone to speak.");
    speakResponse("Neural chat active. How may I assist you?");
  });
  document.getElementById('sc-media').addEventListener('click', () => {
    submitQuery("Open media player");
  });
  // Native OS Toast Notification
  function showToast(msg, icon = 'ℹ️') {
    const container = document.getElementById('os-toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'os-toast';
    toast.innerHTML = `<span style="font-size:16px;">${icon}</span><span>${msg}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(50px)';
      setTimeout(() => toast.remove(), 350);
    }, 4000);
  }

  // Modals
  const modalTerminal = document.getElementById('modal-terminal');
  const termScreen = document.getElementById('term-screen');
  const termHistory = document.getElementById('term-history');
  const termInput = document.getElementById('term-input');
  const termPs1 = document.getElementById('term-ps1');
  let currentTermCwd = '/home/serein';
  let termCmdHistory = [];
  let termHistoryIndex = -1;

  function openTerminal() {
    if (modalTerminal) {
      modalTerminal.classList.add('open');
      if (termInput) termInput.focus();
    }
  }

  if (termInput) {
    termInput.addEventListener('keydown', async (e) => {
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (termCmdHistory.length > 0) {
          if (termHistoryIndex === -1) termHistoryIndex = termCmdHistory.length - 1;
          else if (termHistoryIndex > 0) termHistoryIndex--;
          termInput.value = termCmdHistory[termHistoryIndex] || '';
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (termHistoryIndex !== -1) {
          if (termHistoryIndex < termCmdHistory.length - 1) {
            termHistoryIndex++;
            termInput.value = termCmdHistory[termHistoryIndex] || '';
          } else {
            termHistoryIndex = -1;
            termInput.value = '';
          }
        }
      } else if (e.key === 'Enter') {
        const cmd = termInput.value.trim();
        termInput.value = '';
        if (!cmd) return;
        termCmdHistory.push(cmd);
        termHistoryIndex = -1;

        const row = document.createElement('div');
        row.className = 'term-cmd-row';
        row.innerHTML = `<span class="term-cmd-ps1">${termPs1.textContent}</span> <span class="term-cmd-text">${cmd}</span>`;
        termHistory.appendChild(row);

        if (cmd === 'clear') {
          termHistory.innerHTML = '';
          return;
        }

        try {
          const res = await fetch('/api/terminal/exec', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd, cwd: currentTermCwd})
          });
          const data = await res.json();
          if (data.cwd) {
            currentTermCwd = data.cwd;
            const dirName = currentTermCwd === '/home/serein' ? '~' : currentTermCwd.split(/[\\/]/).pop() || '/';
            termPs1.textContent = `serein@sereinos:${dirName}$`;
          }
          if (data.stdout) {
            const out = document.createElement('div');
            out.className = 'term-out-stdout';
            out.textContent = data.stdout;
            termHistory.appendChild(out);
          }
          if (data.stderr) {
            const err = document.createElement('div');
            err.className = 'term-out-stderr';
            err.textContent = data.stderr;
            termHistory.appendChild(err);
          }
        } catch (error) {
          const errDiv = document.createElement('div');
          errDiv.className = 'term-out-stderr';
          errDiv.textContent = `Terminal error: ${error.message}\n`;
          termHistory.appendChild(errDiv);
        }
        termScreen.scrollTop = termScreen.scrollHeight;
      }
    });
  }

  // File Explorer
  const modalFiles = document.getElementById('modal-files');
  const filesGrid = document.getElementById('files-grid');
  const filesPathBar = document.getElementById('files-current-path');
  const filesBtnBack = document.getElementById('files-btn-back');
  const filesBtnHome = document.getElementById('files-btn-home');
  const filesBtnRefresh = document.getElementById('files-btn-refresh');
  const filesViewerPanel = document.getElementById('files-viewer-panel');
  const viewerFilename = document.getElementById('viewer-filename');
  const viewerContent = document.getElementById('viewer-content');
  const btnCloseViewer = document.getElementById('btn-close-viewer');

  let currentFilesPath = '/home/serein';
  let currentFilesParent = '/home';

  async function loadFiles(path) {
    if (!filesGrid) return;
    filesGrid.innerHTML = '<div class="files-loading">Reading filesystem...</div>';
    try {
      const res = await fetch('/api/files/list', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: path})
      });
      const data = await res.json();
      currentFilesPath = data.path;
      currentFilesParent = data.parent;
      if (filesPathBar) filesPathBar.textContent = currentFilesPath;
      filesGrid.innerHTML = '';
      if (!data.items || data.items.length === 0) {
        filesGrid.innerHTML = '<div style="color: var(--text-dim); padding: 20px;">Directory is empty</div>';
        return;
      }
      data.items.forEach(item => {
        const el = document.createElement('div');
        el.className = 'file-item';
        el.innerHTML = `
          <div class="file-item-icon">${item.is_dir ? '📁' : (item.name.endsWith('.sh') || item.name.endsWith('.py') ? '⚙️' : '📄')}</div>
          <div class="file-item-name">${item.name}</div>
          <div class="file-item-meta">${item.is_dir ? 'Folder' : formatFileSize(item.size)}</div>
        `;
        el.addEventListener('click', () => {
          if (item.is_dir) {
            loadFiles(item.path);
          } else {
            openFilePreview(item.path, item.name);
          }
        });
        filesGrid.appendChild(el);
      });
    } catch (e) {
      filesGrid.innerHTML = `<div style="color:#ff5555; padding: 20px;">Error loading directory: ${e.message}</div>`;
    }
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
    return (bytes/(1024*1024)).toFixed(1) + ' MB';
  }

  async function openFilePreview(path, name) {
    if (!filesViewerPanel) return;
    viewerFilename.textContent = name;
    viewerContent.textContent = 'Loading file...';
    filesViewerPanel.style.display = 'flex';
    try {
      const res = await fetch('/api/files/read', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: path})
      });
      const data = await res.json();
      viewerContent.textContent = data.content || (data.error || 'Empty file');
    } catch (err) {
      viewerContent.textContent = 'Could not preview file: ' + err.message;
    }
  }

  if (btnCloseViewer) {
    btnCloseViewer.addEventListener('click', () => {
      if (filesViewerPanel) filesViewerPanel.style.display = 'none';
    });
  }
  if (filesBtnBack) {
    filesBtnBack.addEventListener('click', () => {
      if (currentFilesParent) loadFiles(currentFilesParent);
    });
  }
  if (filesBtnHome) {
    filesBtnHome.addEventListener('click', () => loadFiles('/home/serein'));
  }
  if (filesBtnRefresh) {
    filesBtnRefresh.addEventListener('click', () => loadFiles(currentFilesPath));
  }

  function openFiles() {
    if (modalFiles) {
      modalFiles.classList.add('open');
      loadFiles(currentFilesPath);
    }
  }

  // Native Glass Cyber Browser Modal
  const modalBrowser = document.getElementById('modal-browser');
  const browserIframe = document.getElementById('browser-iframe');
  const browserUrlInput = document.getElementById('browser-url-input');
  const browserBtnGo = document.getElementById('browser-btn-go');
  const browserBtnBack = document.getElementById('browser-btn-back');
  const browserBtnForward = document.getElementById('browser-btn-forward');
  const browserBtnReload = document.getElementById('browser-btn-reload');

  function openBrowser(url) {
    if (modalBrowser) {
      modalBrowser.classList.add('open');
      if (url && browserIframe) {
        browserIframe.src = url;
        if (browserUrlInput) browserUrlInput.value = url;
      }
    }
  }

  function navigateBrowser() {
    if (!browserUrlInput || !browserIframe) return;
    let target = browserUrlInput.value.trim();
    if (!target) return;
    if (!target.startsWith('http://') && !target.startsWith('https://')) {
      if (target.includes('.') && !target.includes(' ')) {
        target = 'https://' + target;
      } else {
        target = 'https://duckduckgo.com/?q=' + encodeURIComponent(target);
      }
    }
    browserUrlInput.value = target;
    browserIframe.src = target;
  }

  if (browserBtnGo) browserBtnGo.addEventListener('click', navigateBrowser);
  if (browserUrlInput) {
    browserUrlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') navigateBrowser();
    });
  }
  if (browserBtnReload && browserIframe) {
    browserBtnReload.addEventListener('click', () => {
      browserIframe.src = browserIframe.src;
    });
  }
  if (browserBtnBack && browserIframe) {
    browserBtnBack.addEventListener('click', () => {
      try { browserIframe.contentWindow.history.back(); } catch (e) {}
    });
  }
  if (browserBtnForward && browserIframe) {
    browserBtnForward.addEventListener('click', () => {
      try { browserIframe.contentWindow.history.forward(); } catch (e) {}
    });
  }
  document.querySelectorAll('.bookmark-item').forEach(b => {
    b.addEventListener('click', () => {
      const u = b.getAttribute('data-url');
      if (u) {
        if (browserUrlInput) browserUrlInput.value = u;
        if (browserIframe) browserIframe.src = u;
      }
    });
  });

  // System Settings Modal
  const modalSettings = document.getElementById('modal-settings');
  const setSliderBrightness = document.getElementById('set-slider-brightness');
  const setBrightnessVal = document.getElementById('set-brightness-val');
  const setSliderVolume = document.getElementById('set-slider-volume');
  const setVolumeVal = document.getElementById('set-volume-val');
  const setBtnMute = document.getElementById('set-btn-mute');
  const setAiProvider = document.getElementById('set-ai-provider');
  const setApiKey = document.getElementById('set-api-key');
  const btnSaveAiSettings = document.getElementById('btn-save-ai-settings');
  const btnOpenWifiFromSettings = document.getElementById('btn-open-wifi-from-settings');

  function openSettings() {
    if (modalSettings) {
      modalSettings.classList.add('open');
      loadSystemSettings();
    }
  }

  async function loadSystemSettings() {
    try {
      const res = await fetch('/api/settings/get');
      if (res.ok) {
        const s = await res.json();
        if (setSliderBrightness) { setSliderBrightness.value = s.brightness || 75; setBrightnessVal.textContent = (s.brightness || 75) + '%'; }
        if (setSliderVolume) { setSliderVolume.value = s.volume || 75; setVolumeVal.textContent = (s.volume || 75) + '%'; }
        if (setAiProvider) setAiProvider.value = s.ai_provider || 'local';
        if (setApiKey) setApiKey.value = s.api_key || '';
      }
    } catch (e) {}
  }

  document.querySelectorAll('.settings-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.settings-tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.settings-pane').forEach(p => p.style.display = 'none');
      btn.classList.add('active');
      const tabId = btn.getAttribute('data-tab');
      const pane = document.getElementById(tabId);
      if (pane) pane.style.display = 'block';
    });
  });

  if (btnSaveAiSettings) {
    btnSaveAiSettings.addEventListener('click', async () => {
      const prov = setAiProvider ? setAiProvider.value : 'local';
      const key = setApiKey ? setApiKey.value.trim() : '';
      try {
        await fetch('/api/settings/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ai_provider: prov, api_key: key})
        });
        showToast('AI engine settings saved successfully!', '🧠');
        appendDialogue('Serein', `AI configuration updated: active provider is ${prov}.`);
        speakResponse(`AI configuration updated: active provider is ${prov}.`);
      } catch (err) {
        showToast('Failed to save settings: ' + err.message, '⚠️');
      }
    });
  }

  if (btnOpenWifiFromSettings) {
    btnOpenWifiFromSettings.addEventListener('click', () => {
      if (modalSettings) modalSettings.classList.remove('open');
      if (modalWifi) {
        modalWifi.classList.add('open');
        scanWifiNetworks();
      }
    });
  }

  const btnTestVoice = document.getElementById('btn-test-voice-audio');
  if (btnTestVoice) {
    btnTestVoice.addEventListener('click', () => {
      showToast('Synthesizing audio test...', '🔊');
      speakResponse("Hello Kai! SereinOS voice synthesis and audio playback are operating smoothly.");
    });
  }

  // Power Options Modal
  const modalPower = document.getElementById('modal-power');
  const btnPowerShutdown = document.getElementById('btn-power-shutdown');
  const btnPowerRestart = document.getElementById('btn-power-restart');
  const btnPowerSleep = document.getElementById('btn-power-sleep');
  const btnPowerCancel = document.getElementById('btn-power-cancel');

  function openPower() {
    if (modalPower) modalPower.classList.add('open');
  }

  if (btnPowerCancel) {
    btnPowerCancel.addEventListener('click', () => {
      if (modalPower) modalPower.classList.remove('open');
    });
  }

  if (btnPowerShutdown) {
    btnPowerShutdown.addEventListener('click', () => {
      if (modalPower) modalPower.classList.remove('open');
      showToast('Shutting down SereinOS... Goodbye, Kai.', '🛑');
      appendDialogue('Serein', "Shutting down SereinOS. Powering off system safely. Goodbye, Kai.");
      speakResponse("Shutting down SereinOS. Goodbye, Kai.");
      setTimeout(() => {
        fetch('/api/system/power', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'poweroff'}) });
      }, 1200);
    });
  }

  if (btnPowerRestart) {
    btnPowerRestart.addEventListener('click', () => {
      if (modalPower) modalPower.classList.remove('open');
      showToast('Restarting SereinOS...', '🔄');
      appendDialogue('Serein', "Restarting SereinOS. Rebooting system now.");
      speakResponse("Restarting SereinOS. Rebooting now.");
      setTimeout(() => {
        fetch('/api/system/power', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'reboot'}) });
      }, 1200);
    });
  }

  if (btnPowerSleep) {
    btnPowerSleep.addEventListener('click', () => {
      if (modalPower) modalPower.classList.remove('open');
      showToast('Suspending SereinOS to RAM...', '🌙');
      appendDialogue('Serein', "Putting system into sleep mode.");
      setTimeout(() => {
        fetch('/api/system/power', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'suspend'}) });
      }, 800);
    });
  }

  // Hook all Shortcuts and Start Menu actions
  document.getElementById('sc-files').addEventListener('click', () => {
    openFiles();
  });
  document.getElementById('sc-settings').addEventListener('click', () => {
    openSettings();
  });

  // 10. Start Menu Toggle
  startBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    startMenu.classList.toggle('open');
  });

  document.addEventListener('click', (e) => {
    if (!startMenu.contains(e.target) && e.target !== startBtn) {
      startMenu.classList.remove('open');
    }
  });

  document.getElementById('start-terminal').addEventListener('click', () => {
    openTerminal();
    startMenu.classList.remove('open');
  });
  document.getElementById('start-browser').addEventListener('click', () => {
    openBrowser();
    startMenu.classList.remove('open');
  });
  document.getElementById('start-files').addEventListener('click', () => {
    openFiles();
    startMenu.classList.remove('open');
  });
  document.getElementById('start-wifi').addEventListener('click', () => {
    modalWifi.classList.add('open');
    scanWifiNetworks();
    startMenu.classList.remove('open');
  });
  document.getElementById('start-settings').addEventListener('click', () => {
    openSettings();
    startMenu.classList.remove('open');
  });
  document.getElementById('start-power').addEventListener('click', () => {
    openPower();
    startMenu.classList.remove('open');
  });

  // Dock Icons
  document.getElementById('dock-serein').addEventListener('click', () => {
    userInput.focus();
    appendDialogue('Serein', "I am here, Kai.");
    speakResponse("I am here, Kai.");
  });
  document.getElementById('dock-browser').addEventListener('click', () => {
    openBrowser();
  });
  document.getElementById('dock-terminal').addEventListener('click', () => {
    openTerminal();
  });
  document.getElementById('dock-mail').addEventListener('click', () => {
    appendDialogue('Serein', "No new messages in local inbox.");
    speakResponse("No new messages in your inbox.");
  });
  document.getElementById('dock-gallery').addEventListener('click', () => {
    appendDialogue('Serein', "Opening Media Gallery.");
  });
  document.getElementById('dock-settings').addEventListener('click', () => {
    openSettings();
  });

  // Top Bar Indicators
  wifiIndicator.addEventListener('click', () => {
    modalWifi.classList.add('open');
    scanWifiNetworks();
  });

  const topSettings = document.getElementById('top-settings');
  if (topSettings) {
    topSettings.addEventListener('click', () => {
      openSettings();
    });
  }

  const topFullscreen = document.getElementById('top-fullscreen');
  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(() => {});
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
    }
  }
  if (topFullscreen) {
    topFullscreen.addEventListener('click', toggleFullscreen);
  }

  // Prevent browser context menu on desktop
  document.addEventListener('contextmenu', (e) => {
    if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
    }
  });

  // 1. Completely disable browser zoom & web artifacts (Ctrl++, Ctrl+-, Ctrl+0, Ctrl+Wheel)
  window.addEventListener('keydown', (e) => {
    // Intercept Zoom shortcuts: Ctrl/Cmd + Plus, Minus, Equals, Underscore, Zero, NumpadAdd, NumpadSubtract
    if (e.ctrlKey || e.metaKey) {
      if (
        e.key === '+' || e.key === '-' || e.key === '=' || e.key === '_' || e.key === '0' ||
        e.code === 'NumpadAdd' || e.code === 'NumpadSubtract' || e.code === 'Equal' || e.code === 'Minus' || e.code === 'Digit0' || e.code === 'Numpad0'
      ) {
        e.preventDefault();
        e.stopPropagation();
        return false;
      }

      // Also intercept web browser default actions that don't belong in an OS desktop:
      // Ctrl+S (Save webpage), Ctrl+P (Print page), Ctrl+U (View source)
      if (['s', 'S', 'p', 'P', 'u', 'U'].includes(e.key)) {
        e.preventDefault();
        e.stopPropagation();
        return false;
      }
    }

    // Intercept Caret Browsing (F7) and Browser Search (F3)
    if (e.key === 'F7' || e.key === 'F3') {
      e.preventDefault();
      e.stopPropagation();
      return false;
    }

    // Alt + Left/Right Arrow (Browser history navigation)
    if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      e.stopPropagation();
      return false;
    }

    // Native Desktop OS Shortcuts
    if (e.key === 'F11') {
      e.preventDefault();
      toggleFullscreen();
    } else if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
      if (startMenu) startMenu.classList.remove('open');
    }
  }, { capture: true, passive: false });

  // Disable Ctrl + Mouse Wheel zooming
  window.addEventListener('wheel', (e) => {
    if (e.ctrlKey) {
      e.preventDefault();
    }
  }, { passive: false });

  // Disable gesture zoom on multi-touch trackpads
  document.addEventListener('gesturestart', (e) => e.preventDefault());
  document.addEventListener('gesturechange', (e) => e.preventDefault());
  document.addEventListener('gestureend', (e) => e.preventDefault());

  // Ensure window stays pinned at (0, 0)
  window.scrollTo(0, 0);
  window.addEventListener('resize', () => window.scrollTo(0, 0));

  const volumeIndicator = document.getElementById('volume-indicator');
  if (volumeIndicator) {
    volumeIndicator.addEventListener('click', () => {
      const modalQC = document.getElementById('modal-quick-controls');
      if (modalQC) modalQC.classList.add('open');
    });
  }

  // Modal Closers
  document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', () => {
      const modalId = btn.getAttribute('data-close');
      document.getElementById(modalId).classList.remove('open');
    });
  });

  // 11. WiFi Network Scanning & Connection
  async function scanWifiNetworks() {
    wifiSpinner.style.display = 'inline';
    wifiList.innerHTML = '<div class="wifi-item placeholder">Scanning for available networks...</div>';
    try {
      const res = await fetch('/api/wifi/scan');
      if (res.ok) {
        const networks = await res.json();
        wifiList.innerHTML = '';
        if (networks.length === 0) {
          wifiList.innerHTML = `
            <div style="background: rgba(32, 226, 163, 0.1); border: 1px solid var(--accent-mint); border-radius: 8px; padding: 12px; margin-bottom: 8px; font-size: 12px; line-height: 1.4;">
              <div style="color: var(--accent-mint); font-weight: 600; margin-bottom: 4px;">🌐 Virtual Ethernet Active (Wired NAT)</div>
              <div style="color: rgba(255,255,255,0.85);">Virtual machines (VMware/VirtualBox) route network traffic through virtual Ethernet (<code>ens33</code>) rather than exposing physical Wi-Fi hardware. Your internet connection is active! To scan physical Wi-Fi inside a VM, attach a USB Wi-Fi dongle via <em>VM &gt; Removable Devices</em>. On physical laptops, Wi-Fi networks appear automatically.</div>
            </div>
          `;
        } else {
          const hasVirtual = networks.some(n => n.is_virtual || (n.ssid && n.ssid.includes('Virtual Demo')));
          if (hasVirtual) {
            const vmBanner = document.createElement('div');
            vmBanner.style.cssText = 'background: rgba(32, 226, 163, 0.1); border: 1px solid var(--accent-mint); border-radius: 8px; padding: 10px; margin-bottom: 10px; font-size: 11px; line-height: 1.4;';
            vmBanner.innerHTML = `
              <div style="color: var(--accent-mint); font-weight: 600; margin-bottom: 2px;">🌐 Virtual Machine Mode: Connected via Virtual Ethernet</div>
              <div style="color: rgba(255,255,255,0.8);">Virtual machines use virtual Ethernet (<code>ens33</code>). Internet is online. Demo networks are shown below for UI testing. On physical hardware, real wireless SSIDs are scanned automatically.</div>
            `;
            wifiList.appendChild(vmBanner);
          }
          networks.forEach(net => {
            const item = document.createElement('div');
            item.className = 'wifi-item';
            item.innerHTML = `
              <span><strong>${net.ssid}</strong></span>
              <span style="color: var(--accent-mint); font-size: 11px;">${net.signal}% ${net.security ? '🔒' : '🔓'}</span>
            `;
            item.addEventListener('click', () => {
              selectedSsid = net.ssid;
              selectedSsidEl.textContent = net.ssid;
              wifiConnectBox.style.display = 'flex';
              wifiPassword.focus();
            });
            wifiList.appendChild(item);
          });
        }
      }
    } catch (e) {
      wifiList.innerHTML = '<div class="wifi-item placeholder">WiFi scanner error</div>';
    } finally {
      wifiSpinner.style.display = 'none';
    }
  }

  document.getElementById('btn-wifi-refresh').addEventListener('click', scanWifiNetworks);
  document.getElementById('btn-wifi-connect').addEventListener('click', async () => {
    if (!selectedSsid) return;
    const btn = document.getElementById('btn-wifi-connect');
    btn.textContent = 'Connecting...';
    btn.disabled = true;
    try {
      const res = await fetch('/api/wifi/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ssid: selectedSsid, password: wifiPassword.value})
      });
      const data = await res.json();
      if (data.success) {
        appendDialogue('Serein', `Connected to ${selectedSsid}.`);
        speakResponse(`Connected to ${selectedSsid}.`);
        showToast(`Connected to ${selectedSsid}`, '📶');
        modalWifi.classList.remove('open');
      } else {
        showToast(data.error || 'Connection failed', '⚠️');
      }
    } catch (e) {
      showToast('Network error during connection', '⚠️');
    } finally {
      btn.textContent = 'Connect';
      btn.disabled = false;
    }
  });

  // 13. Sony VAIO Hardware Controls (Brightness & Audio Volume Sliders)
  const sliderBrightness = document.getElementById('slider-brightness');
  const modalSliderBrightness = document.getElementById('modal-slider-brightness');
  const valBrightness = document.getElementById('val-brightness');
  const modalValBrightness = document.getElementById('modal-val-brightness');

  const sliderVolume = document.getElementById('slider-volume');
  const modalSliderVolume = document.getElementById('modal-slider-volume');
  const valVolume = document.getElementById('val-volume');
  const modalValVolume = document.getElementById('modal-val-volume');

  const btnVolumeMute = document.getElementById('btn-volume-mute');
  const modalBtnMute = document.getElementById('modal-btn-mute');
  const modalQuickControls = document.getElementById('modal-quick-controls');

  function updateBrightnessUI(val) {
    currentBrightness = Math.max(10, Math.min(100, parseInt(val) || 75));
    if (sliderBrightness) sliderBrightness.value = currentBrightness;
    if (modalSliderBrightness) modalSliderBrightness.value = currentBrightness;
    if (valBrightness) valBrightness.textContent = `${currentBrightness}%`;
    if (modalValBrightness) modalValBrightness.textContent = `${currentBrightness}%`;

    // Apply real-time visual brightness filter across entire viewport
    const visualFactor = 0.35 + (currentBrightness / 100) * 0.65;
    document.documentElement.style.filter = `brightness(${visualFactor.toFixed(2)})`;
  }

  function updateVolumeUI(val, muted) {
    currentVolume = Math.max(0, Math.min(100, parseInt(val) || 75));
    if (muted !== undefined) isMuted = Boolean(muted);
    if (sliderVolume) sliderVolume.value = currentVolume;
    if (modalSliderVolume) modalSliderVolume.value = currentVolume;
    if (valVolume) valVolume.textContent = isMuted ? 'Muted' : `${currentVolume}%`;
    if (modalValVolume) modalValVolume.textContent = isMuted ? 'Muted' : `${currentVolume}%`;

    const icon = isMuted ? '🔇' : (currentVolume === 0 ? '🔈' : '🔊');
    if (btnVolumeMute) {
      btnVolumeMute.textContent = icon;
      btnVolumeMute.classList.toggle('muted', isMuted);
    }
    if (modalBtnMute) {
      modalBtnMute.textContent = icon;
      modalBtnMute.classList.toggle('muted', isMuted);
    }

    // Live update Web Audio master gain node
    if (audioContext && masterGainNode) {
      const effectiveGain = isMuted ? 0 : Math.max(0, Math.min(1, currentVolume / 100));
      masterGainNode.gain.setValueAtTime(effectiveGain, audioContext.currentTime);
    }
  }

  async function persistBrightness(val) {
    try {
      localStorage.setItem('serein_brightness', String(val));
      await fetch('/api/system/brightness', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brightness_percent: parseInt(val) })
      });
    } catch (e) {
      console.warn('Brightness persist note:', e);
    }
  }

  async function persistVolume(val, muted) {
    try {
      localStorage.setItem('serein_volume', String(val));
      localStorage.setItem('serein_muted', String(muted));
      await fetch('/api/system/volume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ volume_percent: parseInt(val), muted: muted })
      });
    } catch (e) {
      console.warn('Volume persist note:', e);
    }
  }

  // Bind live slider listeners
  [sliderBrightness, modalSliderBrightness].forEach(el => {
    if (!el) return;
    el.addEventListener('input', (e) => {
      updateBrightnessUI(e.target.value);
    });
    el.addEventListener('change', (e) => {
      persistBrightness(e.target.value);
    });
  });

  [sliderVolume, modalSliderVolume].forEach(el => {
    if (!el) return;
    el.addEventListener('input', (e) => {
      updateVolumeUI(e.target.value, isMuted);
    });
    el.addEventListener('change', (e) => {
      persistVolume(e.target.value, isMuted);
      playAudioFeedbackChime();
    });
  });

  // Mute toggle listeners
  [btnVolumeMute, modalBtnMute].forEach(btn => {
    if (!btn) return;
    btn.addEventListener('click', () => {
      isMuted = !isMuted;
      updateVolumeUI(currentVolume, isMuted);
      persistVolume(currentVolume, isMuted);
    });
  });

  // Top Bar Volume button opens Quick Controls modal
  if (volumeIndicator && modalQuickControls) {
    volumeIndicator.addEventListener('click', () => {
      modalQuickControls.classList.add('open');
    });
  }

  if (topSettings && modalQuickControls) {
    topSettings.addEventListener('click', () => {
      modalQuickControls.classList.add('open');
    });
  }

  // Load hardware status from backend on boot
  async function initHardwareControls() {
    try {
      const bRes = await fetch('/api/system/brightness');
      if (bRes.ok) {
        const bData = await bRes.json();
        if (bData.brightness_percent !== undefined) {
          updateBrightnessUI(bData.brightness_percent);
        }
      }
    } catch (e) {
      const savedB = localStorage.getItem('serein_brightness');
      if (savedB) updateBrightnessUI(savedB);
    }

    try {
      const vRes = await fetch('/api/system/volume');
      if (vRes.ok) {
        const vData = await vRes.json();
        if (vData.volume_percent !== undefined) {
          updateVolumeUI(vData.volume_percent, vData.muted);
        }
      }
    } catch (e) {
      const savedV = localStorage.getItem('serein_volume');
      const savedM = localStorage.getItem('serein_muted') === 'true';
      if (savedV) updateVolumeUI(savedV, savedM);
    }

    // Ensure audio volume is not 0% on boot (VMware ALSA unmuted default)
    if (currentVolume <= 0 && !isMuted) {
      updateVolumeUI(80, false);
    }
    if (currentBrightness <= 0) {
      updateBrightnessUI(80);
    }

    // Auto-detect and populate live system hardware specs
    try {
      const hRes = await fetch('/api/system/hardware');
      if (hRes.ok) {
        const hData = await hRes.json();
        const live = hData.live_hardware || {};
        const sys = hData.system || {};

        const machineName = live.machine || sys.detected_machine || 'x86_64 Machine';
        const cpuName = live.cpu || sys.detected_cpu || 'x86_64 Processor';
        const gpuName = live.gpu || sys.detected_gpu || 'Hardware Accelerated Graphics';
        const ramName = live.ram || (sys.detected_ram_gb ? sys.detected_ram_gb + ' GB' : '4.0 GB');
        const audioName = live.audio || sys.detected_audio || 'PipeWire Audio';
        const archName = sys.architecture || 'x86_64 (AMD64)';

        // Populate Quick Controls Modal
        const qMachine = document.getElementById('qc-hw-machine');
        const qCpu = document.getElementById('qc-hw-cpu');
        const qGpu = document.getElementById('qc-hw-gpu');
        const qRam = document.getElementById('qc-hw-ram');
        const qAudio = document.getElementById('qc-hw-audio');

        if (qMachine) qMachine.textContent = machineName;
        if (qCpu) qCpu.textContent = cpuName;
        if (qGpu) qGpu.textContent = gpuName;
        if (qRam) qRam.textContent = ramName;
        if (qAudio) qAudio.textContent = audioName;

        // Populate Settings About Tab
        const aArch = document.getElementById('about-arch');
        const aDevice = document.getElementById('about-device');
        const aCpu = document.getElementById('about-cpu');
        const aGpu = document.getElementById('about-gpu');
        const aRam = document.getElementById('about-ram');
        const aAudio = document.getElementById('about-audio');

        if (aArch) aArch.textContent = archName;
        if (aDevice) aDevice.textContent = machineName;
        if (aCpu) aCpu.textContent = cpuName;
        if (aGpu) aGpu.textContent = gpuName;
        if (aRam) aRam.textContent = ramName;
        if (aAudio) aAudio.textContent = audioName;

        // Populate Settings Display & Audio badges
        const setGpuBadge = document.getElementById('set-gpu-badge');
        const setAudioBadge = document.getElementById('set-audio-device-badge');
        if (setGpuBadge) setGpuBadge.textContent = gpuName;
        if (setAudioBadge) setAudioBadge.textContent = audioName;
      }
    } catch (e) {
      console.warn('Live hardware detection note:', e);
    }
  }

  initHardwareControls();

  // =========================================================================
  // 12. Standalone OS Disk Installer Wizard
  // =========================================================================
  const modalInstaller = document.getElementById('modal-installer');
  const desktopInstallerShortcut = document.getElementById('desktop-installer-shortcut');
  const menuInstallerShortcut = document.getElementById('menu-installer-shortcut');
  const installerDiskSelect = document.getElementById('installer-disk-select');
  const installerFirmwareSelect = document.getElementById('installer-firmware-select');
  const installerDiskModel = document.getElementById('installer-disk-model');
  const installerDiskCapacity = document.getElementById('installer-disk-capacity');
  const installerDiskPttable = document.getElementById('installer-disk-pttable');
  const btnInstallerNext = document.getElementById('btn-installer-next');
  const btnInstallerBack = document.getElementById('btn-installer-back');
  const btnInstallerDryRun = document.getElementById('btn-installer-dryrun');
  const btnInstallerStart = document.getElementById('btn-installer-start');
  const btnInstallerReboot = document.getElementById('btn-installer-reboot');
  const btnInstallerFinish = document.getElementById('btn-installer-finish');
  const installerProgressBar = document.getElementById('installer-progress-bar');
  const installerProgressPct = document.getElementById('installer-progress-pct');
  const installerPhase = document.getElementById('installer-phase');
  const installerTerminalLog = document.getElementById('installer-terminal-log');
  const installerNavFooter = document.getElementById('installer-nav-footer');

  let currentStep = 1;
  let cachedDisks = [];
  let installPollInterval = null;

  function setWizardStep(step) {
    currentStep = step;
    document.querySelectorAll('.installer-step').forEach((el, idx) => {
      el.style.display = (idx + 1 === step) ? 'block' : 'none';
      if (idx + 1 === step) el.classList.add('active');
      else el.classList.remove('active');
    });

    document.querySelectorAll('.step-bullet').forEach((el) => {
      const s = parseInt(el.getAttribute('data-step') || '1', 10);
      if (s === step) {
        el.classList.add('active');
      } else if (s < step) {
        el.classList.add('completed');
        el.classList.remove('active');
      } else {
        el.classList.remove('active', 'completed');
      }
    });

    if (step === 1) {
      if (btnInstallerBack) btnInstallerBack.style.display = 'none';
      if (btnInstallerNext) {
        btnInstallerNext.style.display = 'inline-block';
        btnInstallerNext.textContent = 'Next Step →';
      }
      if (installerNavFooter) installerNavFooter.style.display = 'flex';
    } else if (step === 2) {
      if (btnInstallerBack) btnInstallerBack.style.display = 'inline-block';
      if (btnInstallerNext) {
        btnInstallerNext.style.display = 'inline-block';
        btnInstallerNext.textContent = 'Next Step →';
      }
      if (installerNavFooter) installerNavFooter.style.display = 'flex';
    } else if (step === 3) {
      if (btnInstallerBack) btnInstallerBack.style.display = 'inline-block';
      if (btnInstallerNext) {
        btnInstallerNext.style.display = 'inline-block';
        btnInstallerNext.textContent = 'Proceed to Installation →';
      }
      if (installerNavFooter) installerNavFooter.style.display = 'flex';
    } else if (step === 4) {
      if (installerNavFooter) installerNavFooter.style.display = 'none';
      if (btnInstallerStart) btnInstallerStart.disabled = true;
    } else if (step === 5) {
      if (installerNavFooter) installerNavFooter.style.display = 'none';
    }
  }

  async function openInstallerWizard() {
    setWizardStep(1);
    if (modalInstaller) modalInstaller.classList.add('open');
    if (startMenu) startMenu.classList.remove('open');

    // Run Pre-flight System Checks
    try {
      const statsRes = await fetch('/api/system/stats');
      if (statsRes.ok) {
        const stats = await statsRes.json();
        const chkRam = document.getElementById('chk-ram');
        if (chkRam) chkRam.textContent = `${stats.ram_total_gb || 4.0} GB Total (${stats.ram_used_gb || 1.2} GB in use)`;
        const chkPower = document.getElementById('chk-power');
        if (chkPower) {
          const chg = stats.battery_charging ? 'Connected (Charging)' : 'On Battery';
          chkPower.textContent = `${stats.battery_percent}% • ${chg}`;
        }
      }
    } catch (e) {
      console.log('Pre-flight telemetry note:', e);
    }

    loadInstallerDisks();
  }

  async function loadInstallerDisks() {
    if (!installerDiskSelect) return;
    installerDiskSelect.innerHTML = '<option value="">Scanning storage devices...</option>';
    try {
      const res = await fetch('/api/installer/disks');
      if (res.ok) {
        const data = await res.json();
        cachedDisks = data.disks || [];
        installerDiskSelect.innerHTML = '';

        const installable = cachedDisks.filter(d => !d.is_boot_media);
        if (installable.length === 0) {
          installerDiskSelect.innerHTML = '<option value="">No installable internal disks found</option>';
          updateDiskDetails(null);
          return;
        }

        installable.forEach(d => {
          const opt = document.createElement('option');
          opt.value = d.device;
          opt.textContent = `${d.device} (${d.size_gb} GB - ${d.model})`;
          installerDiskSelect.appendChild(opt);
        });

        cachedDisks.filter(d => d.is_boot_media).forEach(d => {
          const opt = document.createElement('option');
          opt.value = d.device;
          opt.disabled = true;
          opt.textContent = `${d.device} (${d.size_gb} GB - ${d.model}) [LIVE BOOT - PROTECTED]`;
          installerDiskSelect.appendChild(opt);
        });

        updateDiskDetails(installable[0]);
      }
    } catch (err) {
      console.warn('Error fetching disks:', err);
      installerDiskSelect.innerHTML = '<option value="">Error scanning disks</option>';
    }
  }

  function updateDiskDetails(disk) {
    if (!disk) {
      if (installerDiskModel) installerDiskModel.textContent = '--';
      if (installerDiskCapacity) installerDiskCapacity.textContent = '--';
      if (installerDiskPttable) installerDiskPttable.textContent = '--';
      return;
    }
    if (installerDiskModel) installerDiskModel.textContent = disk.model || 'Unknown';
    if (installerDiskCapacity) installerDiskCapacity.textContent = `${disk.size_gb} GB`;
    if (installerDiskPttable) installerDiskPttable.textContent = disk.partition_table ? disk.partition_table.toUpperCase() : 'MBR/GPT';
  }

  if (installerDiskSelect) {
    installerDiskSelect.addEventListener('change', () => {
      const selectedDev = installerDiskSelect.value;
      const disk = cachedDisks.find(d => d.device === selectedDev);
      updateDiskDetails(disk);
    });
  }

  if (desktopInstallerShortcut) {
    desktopInstallerShortcut.addEventListener('click', openInstallerWizard);
  }
  if (menuInstallerShortcut) {
    menuInstallerShortcut.addEventListener('click', openInstallerWizard);
  }

  if (btnInstallerNext) {
    btnInstallerNext.addEventListener('click', () => {
      if (currentStep === 1) {
        setWizardStep(2);
      } else if (currentStep === 2) {
        if (!installerDiskSelect || !installerDiskSelect.value) {
          showToast("Please select a target storage device to continue.", "⚠️");
          return;
        }
        setWizardStep(3);
      } else if (currentStep === 3) {
        const user = (document.getElementById('installer-username')?.value || '').trim();
        const pass = (document.getElementById('installer-password')?.value || '').trim();
        const host = (document.getElementById('installer-hostname')?.value || '').trim();
        if (!user || !pass || !host) {
          showToast("Please fill in username, password, and computer name.", "⚠️");
          return;
        }
        setWizardStep(4);
        runInstallerDryRun();
      }
    });
  }

  if (btnInstallerBack) {
    btnInstallerBack.addEventListener('click', () => {
      if (currentStep > 1 && currentStep <= 3) {
        setWizardStep(currentStep - 1);
      }
    });
  }

  async function runInstallerDryRun() {
    const disk = installerDiskSelect ? installerDiskSelect.value || '/dev/sda' : '/dev/sda';
    const fw = installerFirmwareSelect ? installerFirmwareSelect.value || 'auto' : 'auto';
    if (installerTerminalLog) installerTerminalLog.textContent = `[INFO] Simulating dry-run installation on ${disk} (${fw})...\n`;
    if (installerProgressBar) {
      installerProgressBar.style.width = '30%';
      installerProgressBar.style.background = '';
    }
    if (installerProgressPct) installerProgressPct.textContent = '30%';
    if (installerPhase) {
      installerPhase.textContent = 'Validating installation plan...';
      installerPhase.style.color = '';
    }
    if (btnInstallerStart) btnInstallerStart.disabled = true;

    try {
      const res = await fetch('/api/installer/dry-run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disk: disk, scheme: 'auto', firmware_type: fw })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.valid === true) {
          let logText = `[SUCCESS] ${data.summary}\n\n`;
          logText += `--- Partitioning & Staging Plan ---\n`;
          if (data.steps && data.steps.length) {
            data.steps.forEach(s => { logText += `✔ ${s}\n`; });
          }
          logText += `\n--- Generated /etc/fstab ---\n${data.fstab}\n`;
          logText += `--- Bootloader Staging Command ---\n${data.bootloader}\n\n`;
          logText += `[DRY-RUN COMPLETE] All pre-requisites passed without modifying disk. Click 'Start Real Installation' to proceed.`;

          if (installerTerminalLog) installerTerminalLog.textContent = logText;
          if (installerProgressBar) {
            installerProgressBar.style.width = '100%';
            installerProgressBar.style.background = '#10b981';
          }
          if (installerProgressPct) installerProgressPct.textContent = '100%';
          if (installerPhase) {
            installerPhase.textContent = 'Dry-run validation successful';
            installerPhase.style.color = '#10b981';
          }
          if (btnInstallerStart) btnInstallerStart.disabled = false;
        } else {
          const failMsg = data.summary || data.error || 'Disk validation failed';
          let logText = `[VALIDATION FAILED] ${failMsg}\n\n`;
          if (data.error) logText += `Error: ${data.error}\n`;
          if (installerTerminalLog) installerTerminalLog.textContent = logText;
          if (installerProgressBar) {
            installerProgressBar.style.width = '0%';
            installerProgressBar.style.background = '#ef4444';
          }
          if (installerProgressPct) installerProgressPct.textContent = '0%';
          if (installerPhase) {
            installerPhase.textContent = 'Dry-run validation failed';
            installerPhase.style.color = '#ef4444';
          }
          if (btnInstallerStart) btnInstallerStart.disabled = true;
        }
      } else {
        const err = await res.text();
        if (installerTerminalLog) installerTerminalLog.textContent = `[ERROR] Dry-run validation failed: ${err}`;
        if (installerProgressBar) {
          installerProgressBar.style.width = '0%';
          installerProgressBar.style.background = '#ef4444';
        }
        if (installerProgressPct) installerProgressPct.textContent = '0%';
        if (installerPhase) {
          installerPhase.textContent = 'Dry-run validation failed';
          installerPhase.style.color = '#ef4444';
        }
        if (btnInstallerStart) btnInstallerStart.disabled = true;
      }
    } catch (e) {
      if (installerTerminalLog) installerTerminalLog.textContent = `[ERROR] Network error during dry-run: ${e}`;
      if (installerProgressBar) {
        installerProgressBar.style.width = '0%';
        installerProgressBar.style.background = '#ef4444';
      }
      if (installerProgressPct) installerProgressPct.textContent = '0%';
      if (installerPhase) {
        installerPhase.textContent = 'Dry-run validation failed';
        installerPhase.style.color = '#ef4444';
      }
      if (btnInstallerStart) btnInstallerStart.disabled = true;
    }
  }

  if (btnInstallerDryRun) {
    btnInstallerDryRun.addEventListener('click', runInstallerDryRun);
  }

  if (btnInstallerStart) {
    btnInstallerStart.addEventListener('click', async () => {
      const disk = installerDiskSelect ? installerDiskSelect.value || '/dev/sda' : '/dev/sda';
      const fw = installerFirmwareSelect ? installerFirmwareSelect.value || 'auto' : 'auto';
      const user = (document.getElementById('installer-username')?.value || '').trim() || 'serein';
      const pass = (document.getElementById('installer-password')?.value || '').trim() || 'serein';
      const host = (document.getElementById('installer-hostname')?.value || '').trim() || 'serein-pc';

      showToast(`Beginning installation on ${disk}...`, "💾");

      btnInstallerStart.disabled = true;
      if (btnInstallerDryRun) btnInstallerDryRun.disabled = true;
      if (installerProgressBar) installerProgressBar.style.width = '0%';
      if (installerProgressPct) installerProgressPct.textContent = '0%';
      if (installerPhase) installerPhase.textContent = 'Initiating installation...';
      if (installerTerminalLog) installerTerminalLog.textContent = `[START] Initiating disk install to ${disk}...\n`;

      try {
        const res = await fetch('/api/installer/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            disk: disk,
            user: user,
            password: pass,
            hostname: host,
            firmware_type: fw,
            scheme: 'auto'
          })
        });

        if (res.ok) {
          const data = await res.json();
          const taskId = data.task_id;
          startProgressPolling(taskId);
        } else {
          if (installerTerminalLog) installerTerminalLog.textContent += `[ERROR] Failed to start install task.\n`;
          btnInstallerStart.disabled = false;
          if (btnInstallerDryRun) btnInstallerDryRun.disabled = false;
        }
      } catch (e) {
        if (installerTerminalLog) installerTerminalLog.textContent += `[ERROR] Exception starting install: ${e}\n`;
        btnInstallerStart.disabled = false;
        if (btnInstallerDryRun) btnInstallerDryRun.disabled = false;
      }
    });
  }

  function startProgressPolling(taskId) {
    if (installPollInterval) clearInterval(installPollInterval);
    installPollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/installer/progress?task_id=${encodeURIComponent(taskId || '')}`);
        if (res.ok) {
          const prog = await res.json();
          if (installerProgressBar) installerProgressBar.style.width = `${prog.percent}%`;
          if (installerProgressPct) installerProgressPct.textContent = `${prog.percent}%`;
          if (installerPhase) installerPhase.textContent = `Phase: ${prog.phase.toUpperCase()}`;

          if (prog.log && prog.log.length && installerTerminalLog) {
            installerTerminalLog.textContent = prog.log.join('\n');
            installerTerminalLog.scrollTop = installerTerminalLog.scrollHeight;
          }

          if (prog.done) {
            clearInterval(installPollInterval);
            installPollInterval = null;
            if (btnInstallerStart) btnInstallerStart.disabled = false;
            if (btnInstallerDryRun) btnInstallerDryRun.disabled = false;

            if (prog.error) {
              if (installerPhase) installerPhase.textContent = `Failed: ${prog.error}`;
            } else {
              setWizardStep(5);
            }
          }
        }
      } catch (e) {
        console.warn('Progress poll error:', e);
      }
    }, 400);
  }

  if (btnInstallerReboot) {
    btnInstallerReboot.addEventListener('click', () => {
      fetch('/api/system/power', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'reboot' })
      });
    });
  }

  if (btnInstallerFinish) {
    btnInstallerFinish.addEventListener('click', () => {
      if (modalInstaller) modalInstaller.classList.remove('open');
    });
  }

  // 13. Startup Voice Greeting (Spoken softly upon boot)
  setTimeout(() => {
    speakResponse("Welcome, Kai. How may I assist you this evening?");
  }, 1800);
});

