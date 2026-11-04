// SereinAvatar.cs - Main avatar controller for SereinOS
// Handles VRM loading, expressions, lip-sync, and idle animations

using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;
using VRM;
using UniVRM10;

namespace SereinOS
{
    /// <summary>
    /// Main avatar controller for SereinOS.
    /// Manages VRM model, expressions, lip-sync visemes, and idle animations.
    /// </summary>
    public class SereinAvatar : MonoBehaviour
    {
        [Header("VRM Settings")]
        [SerializeField] private string _vrmPath = "Models/serein.vrm";
        [SerializeField] private bool _loadOnStart = true;
        
        [Header("Expression Settings")]
        [SerializeField] private float _expressionTransitionDuration = 0.3f;
        [SerializeField] private float _blinkIntervalMin = 2f;
        [SerializeField] private float _blinkIntervalMax = 6f;
        [SerializeField] private float _blinkDuration = 0.15f;
        
        [Header("Lip Sync Settings")]
        [SerializeField] private int _visemeSmoothingFrames = 3;
        [SerializeField] private float _mouthOpenSpeed = 10f;
        [SerializeField] private float _mouthCloseSpeed = 8f;
        
        [Header("Idle Animation")]
        [SerializeField] private AnimationClip[] _idleAnimations;
        [SerializeField] private float _idleAnimationInterval = 10f;
        [SerializeField] private float _breathingIntensity = 0.02f;
        
        [Header("Network")]
        [SerializeField] private int _coreServicePort = 8080;
        [SerializeField] private string _coreServiceHost = "localhost";
        
        // Components
        private VRM10Instance _vrmInstance;
        private VRM10Controller _vrmController;
        private Animator _animator;
        
        // Expression management
        private Dictionary<string, float> _targetExpressions = new();
        private Dictionary<string, float> _currentExpressions = new();
        private Dictionary<string, float> _expressionVelocities = new();
        private string _activeExpression = "neutral";
        
        // Lip sync
        private Queue<VisemeFrame> _visemeQueue = new();
        private VisemeFrame _currentViseme = VisemeFrame.Neutral;
        private VisemeFrame _previousViseme = VisemeFrame.Neutral;
        private float _visemeInterpolation = 0f;
        
        // Idle animations
        private float _nextIdleTime;
        private int _currentIdleIndex = 0;
        private float _breathingPhase = 0f;
        
        // Network
        private TcpClient _tcpClient;
        private NetworkStream _networkStream;
        private CancellationTokenSource _networkCts;
        
        // Blink
        private float _nextBlinkTime;
        private bool _isBlinking = false;
        private float _blinkProgress = 0f;
        
        // State
        private bool _isInitialized = false;
        private bool _isSpeaking = false;
        
        public event Action<string> OnExpressionChanged;
        public event Action<VisemeFrame> OnVisemeUpdated;
        public event Action<bool> OnSpeakingStateChanged;
        
        // Viseme frame structure
        [Serializable]
        public struct VisemeFrame
        {
            public float sil, pp, ff, th, dd, kk, ch, ss, nn, rr, aa, e, ih, oh, ou;
            
            public static VisemeFrame Neutral => new VisemeFrame { sil = 1f };
            
            public float GetViseme(int index)
            {
                return index switch
                {
                    0 => sil, 1 => pp, 2 => ff, 3 => th, 4 => dd,
                    5 => kk, 6 => ch, 7 => ss, 8 => nn, 9 => rr,
                    10 => aa, 11 => e, 12 => ih, 13 => oh, 14 => ou,
                    _ => 0f
                };
            }
            
            public void SetViseme(int index, float value)
            {
                switch (index)
                {
                    case 0: sil = value; break;
                    case 1: pp = value; break;
                    case 2: ff = value; break;
                    case 3: th = value; break;
                    case 4: dd = value; break;
                    case 5: kk = value; break;
                    case 6: ch = value; break;
                    case 7: ss = value; break;
                    case 8: nn = value; break;
                    case 9: rr = value; break;
                    case 10: aa = value; break;
                    case 11: e = value; break;
                    case 12: ih = value; break;
                    case 13: oh = value; break;
                    case 14: ou = value; break;
                }
            }
        }
        
        private async void Start()
        {
            if (_loadOnStart)
            {
                await InitializeAsync();
            }
        }
        
        public async Task InitializeAsync()
        {
            try
            {
                // Get components
                _animator = GetComponent<Animator>();
                
                // Load VRM model
                await LoadVRMAsync();
                
                // Initialize expression system
                InitializeExpressions();
                
                // Start subsystems
                StartCoroutine(BlinkRoutine());
                StartCoroutine(IdleAnimationRoutine());
                StartCoroutine(BreathingRoutine());
                
                // Connect to core service
                await ConnectToCoreServiceAsync();
                
                _isInitialized = true;
                Debug.Log("[SereinAvatar] Initialization complete");
            }
            catch (Exception e)
            {
                Debug.LogError($"[SereinAvatar] Initialization failed: {e.Message}");
            }
        }
        
        private async Task LoadVRMAsync()
        {
            var path = System.IO.Path.Combine(Application.streamingAssetsPath, _vrmPath);
            
            if (!System.IO.File.Exists(path))
            {
                // Try Resources folder
                path = _vrmPath.Replace(".vrm", "");
                var asset = Resources.Load<UnityEngine.Object>(path);
                if (asset != null)
                {
                    path = AssetDatabase.GetAssetPath(asset);
                }
            }
            
            if (System.IO.File.Exists(path))
            {
                var bytes = System.IO.File.ReadAllBytes(path);
                var context = new VRM10LoadContext();
                _vrmInstance = await context.LoadAsync(bytes);
                
                _vrmInstance.transform.SetParent(transform, false);
                _vrmInstance.transform.localPosition = Vector3.zero;
                _vrmInstance.transform.localRotation = Quaternion.identity;
                
                _vrmController = _vrmInstance.GetComponent<VRM10Controller>();
                Debug.Log($"[SereinAvatar] VRM loaded: {_vrmInstance.name}");
            }
            else
            {
                Debug.LogError($"[SereinAvatar] VRM file not found: {path}");
            }
        }
        
        private void InitializeExpressions()
        {
            if (_vrmInstance == null) return;
            
            // Initialize all expression weights to 0
            var expressionNames = new[] 
            { 
                "neutral", "happy", "sad", "angry", "surprised", 
                "fear", "disgust", "curious", "concerned", "proud",
                "affectionate", "embarrassed", "thinking", "sleepy"
            };
            
            foreach (var name in expressionNames)
            {
                _targetExpressions[name] = 0f;
                _currentExpressions[name] = 0f;
                _expressionVelocities[name] = 0f;
            }
            
            // Set neutral as default
            _targetExpressions["neutral"] = 1f;
            _activeExpression = "neutral";
        }
        
        private async Task ConnectToCoreServiceAsync()
        {
            _networkCts = new CancellationTokenSource();
            
            try
            {
                _tcpClient = new TcpClient();
                await _tcpClient.ConnectAsync(_coreServiceHost, _coreServicePort);
                _networkStream = _tcpClient.GetStream();
                
                // Start listening for messages
                _ = Task.Run(() => ReceiveLoopAsync(_networkCts.Token));
                
                // Send initialization message
                await SendMessageAsync(new { type = "avatar_ready", version = "0.1.0" });
                
                Debug.Log("[SereinAvatar] Connected to core service");
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[SereinAvatar] Could not connect to core service: {e.Message}");
            }
        }
        
        private async Task ReceiveLoopAsync(CancellationToken token)
        {
            var buffer = new byte[4096];
            
            while (!token.IsCancellationRequested && _tcpClient?.Connected == true)
            {
                try
                {
                    int bytesRead = await _networkStream.ReadAsync(buffer, 0, buffer.Length, token);
                    if (bytesRead == 0) break;
                    
                    var json = System.Text.Encoding.UTF8.GetString(buffer, 0, bytesRead);
                    var message = JsonUtility.FromJson<CoreMessage>(json);
                    
                    MainThreadDispatcher.Instance.Enqueue(() => HandleCoreMessage(message));
                }
                catch (Exception e)
                {
                    if (!token.IsCancellationRequested)
                    {
                        Debug.LogError($"[SereinAvatar] Receive error: {e.Message}");
                    }
                }
            }
        }
        
        private void HandleCoreMessage(CoreMessage message)
        {
            switch (message.type)
            {
                case "expression":
                    SetExpression(message.expression, message.intensity);
                    break;
                case "viseme":
                    EnqueueViseme(message.viseme);
                    break;
                case "speak_start":
                    _isSpeaking = true;
                    OnSpeakingStateChanged?.Invoke(true);
                    break;
                case "speak_end":
                    _isSpeaking = false;
                    OnSpeakingStateChanged?.Invoke(false);
                    break;
                case "idle_animation":
                    TriggerIdleAnimation(message.animation);
                    break;
                case "blink":
                    TriggerBlink();
                    break;
            }
        }
        
        private async Task SendMessageAsync(object message)
        {
            if (_networkStream == null) return;
            
            try
            {
                var json = JsonUtility.ToJson(message);
                var bytes = System.Text.Encoding.UTF8.GetBytes(json + "\n");
                await _networkStream.WriteAsync(bytes, 0, bytes.Length);
            }
            catch (Exception e)
            {
                Debug.LogError($"[SereinAvatar] Send error: {e.Message}");
            }
        }
        
        private void Update()
        {
            if (!_isInitialized) return;
            
            float dt = Time.deltaTime;
            
            // Update expressions
            UpdateExpressions(dt);
            
            // Update lip sync
            UpdateLipSync(dt);
            
            // Apply to VRM
            ApplyExpressionsToVRM();
        }
        
        private void UpdateExpressions(float dt)
        {
            foreach (var kvp in _targetExpressions)
            {
                string name = kvp.Key;
                float target = kvp.Key;
                float current = _currentExpressions.GetValueOrDefault(name, 0f);
                float velocity = _expressionVelocities.GetValueOrDefault(name, 0f);
                
                // Smooth damp
                float newCurrent = Mathf.SmoothDamp(current, target, ref velocity, _expressionTransitionDuration, Mathf.Infinity, dt);
                
                _currentExpressions[name] = newCurrent;
                _expressionVelocities[name] = velocity;
            }
        }
        
        private void UpdateLipSync(float dt)
        {
            // Dequeue next viseme if current is done
            if (_visemeInterpolation >= 1f && _visemeQueue.Count > 0)
            {
                _previousViseme = _currentViseme;
                _currentViseme = _visemeQueue.Dequeue();
                _visemeInterpolation = 0f;
            }
            
            // Interpolate visemes
            if (_visemeInterpolation < 1f)
            {
                float speed = _isSpeaking ? _mouthOpenSpeed : _mouthCloseSpeed;
                _visemeInterpolation += dt * speed;
                _visemeInterpolation = Mathf.Clamp01(_visemeInterpolation);
            }
            
            // Notify update
            OnVisemeUpdated?.Invoke(GetInterpolatedViseme());
        }
        
        private VisemeFrame GetInterpolatedViseme()
        {
            var result = new VisemeFrame();
            float t = _visemeInterpolation;
            
            // Smooth step interpolation
            float smoothT = t * t * (3f - 2f * t);
            
            for (int i = 0; i < 15; i++)
            {
                float prev = _previousViseme.GetViseme(i);
                float curr = _currentViseme.GetViseme(i);
                result.SetViseme(i, Mathf.Lerp(prev, curr, smoothT));
            }
            
            return result;
        }
        
        private void ApplyExpressionsToVRM()
        {
            if (_vrmInstance == null || _vrmInstance.Expression == null) return;
            
            foreach (var kvp in _currentExpressions)
            {
                var clip = _vrmInstance.Expression.GetExpression(kvp.Key);
                if (clip != null)
                {
                    clip.Weight = kvp.Value;
                }
            }
        }
        
        public void SetExpression(string expression, float intensity = 1f)
        {
            if (!_targetExpressions.ContainsKey(expression))
            {
                Debug.LogWarning($"[SereinAvatar] Unknown expression: {expression}");
                return;
            }
            
            // Reset all expressions
            foreach (var key in _targetExpressions.Keys.ToList())
            {
                _targetExpressions[key] = 0f;
            }
            
            // Set new expression
            _targetExpressions[expression] = Mathf.Clamp01(intensity);
            _targetExpressions["neutral"] = 1f - intensity * 0.5f; // Blend with neutral
            
            _activeExpression = expression;
            OnExpressionChanged?.Invoke(expression);
        }
        
        public void EnqueueViseme(VisemeFrame viseme)
        {
            _visemeQueue.Enqueue(viseme);
        }
        
        public void EnqueueVisemes(IEnumerable<VisemeFrame> visemes)
        {
            foreach (var v in visemes)
            {
                _visemeQueue.Enqueue(v);
            }
        }
        
        public void TriggerBlink()
        {
            if (!_isBlinking)
            {
                StartCoroutine(BlinkCoroutine());
            }
        }
        
        private IEnumerator BlinkCoroutine()
        {
            _isBlinking = true;
            float elapsed = 0f;
            
            // Close
            while (elapsed < _blinkDuration)
            {
                elapsed += Time.deltaTime;
                float progress = elapsed / _blinkDuration;
                _vrmInstance.Expression.SetExpression("blink", progress);
                yield return null;
            }
            
            // Open
            elapsed = 0f;
            while (elapsed < _blinkDuration)
            {
                elapsed += Time.deltaTime;
                float progress = 1f - (elapsed / _blinkDuration);
                _vrmInstance.Expression.SetExpression("blink", progress);
                yield return null;
            }
            
            _vrmInstance.Expression.SetExpression("blink", 0f);
            _isBlinking = false;
        }
        
        private IEnumerator BlinkRoutine()
        {
            while (true)
            {
                float interval = UnityEngine.Random.Range(_blinkIntervalMin, _blinkIntervalMax);
                yield return new WaitForSeconds(interval);
                
                if (!_isBlinking && _isInitialized)
                {
                    TriggerBlink();
                }
            }
        }
        
        private IEnumerator IdleAnimationRoutine()
        {
            while (true)
            {
                yield return new WaitForSeconds(_idleAnimationInterval);
                
                if (_idleAnimations.Length > 0 && _animator != null && !_isSpeaking)
                {
                    _currentIdleIndex = (_currentIdleIndex + 1) % _idleAnimations.Length;
                    _animator.CrossFade(_idleAnimations[_currentIdleIndex].name, 0.5f);
                }
            }
        }
        
        private IEnumerator BreathingRoutine()
        {
            while (true)
            {
                _breathingPhase += Time.deltaTime * 0.5f;
                
                if (_vrmInstance?.Expression != null)
                {
                    float breath = Mathf.Sin(_breathingPhase) * _breathingIntensity;
                    _vrmInstance.Expression.SetExpression("breathing", Mathf.Clamp01(0.5f + breath));
                }
                
                yield return null;
            }
        }
        
        public void TriggerIdleAnimation(string animationName = null)
        {
            if (_animator == null) return;
            
            if (string.IsNullOrEmpty(animationName) && _idleAnimations.Length > 0)
            {
                _animator.CrossFade(_idleAnimations[_currentIdleIndex].name, 0.5f);
            }
            else if (!string.IsNullOrEmpty(animationName))
            {
                _animator.CrossFade(animationName, 0.5f);
            }
        }
        
        private void OnDestroy()
        {
            _networkCts?.Cancel();
            _networkStream?.Close();
            _tcpClient?.Close();
        }
    }
    
    // Core message types
    [Serializable]
    public class CoreMessage
    {
        public string type;
        public string expression;
        public float intensity;
        public VisemeFrame viseme;
        public string animation;
    }
    
    // Main thread dispatcher for Unity
    public class MainThreadDispatcher : MonoBehaviour
    {
        private static MainThreadDispatcher _instance;
        private readonly Queue<Action> _actions = new Queue<Action>();
        private readonly object _lock = new object();
        
        public static MainThreadDispatcher Instance
        {
            get
            {
                if (_instance == null)
                {
                    var go = new GameObject("MainThreadDispatcher");
                    _instance = go.AddComponent<MainThreadDispatcher>();
                    DontDestroyOnLoad(go);
                }
                return _instance;
            }
        }
        
        public void Enqueue(Action action)
        {
            lock (_lock)
            {
                _actions.Enqueue(action);
            }
        }
        
        private void Update()
        {
            lock (_lock)
            {
                while (_actions.Count > 0)
                {
                    _actions.Dequeue()?.Invoke();
                }
            }
        }
    }
}