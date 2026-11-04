using UnityEngine;
using VRM;
using System;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Serein.Avatar
{
    public class SereinAvatarController : MonoBehaviour
    {
        [Header("Network Settings")]
        [SerializeField] private string serverHost = "127.0.0.1";
        [SerializeField] private int serverPort = 8765;
        [SerializeField] private float reconnectInterval = 5f;

        [Header("Avatar Settings")]
        [SerializeField] private VRMBlendShapeProxy blendShapeProxy;
        [SerializeField] private Animator animator;

        private TcpClient tcpClient;
        private NetworkStream networkStream;
        private CancellationTokenSource cancellationTokenSource;
        private bool isConnected = false;
        private Queue<string> messageQueue = new Queue<string>();
        private readonly object queueLock = new object();

        // Viseme mapping (Azure TTS 22 visemes)
        private readonly Dictionary<string, BlendShapePreset> visemeMap = new Dictionary<string, BlendShapePreset>
        {
            { "sil", BlendShapePreset.A }, { "ae", BlendShapePreset.AA }, { "ax", BlendShapePreset.AA },
            { "ah", BlendShapePreset.AA }, { "ao", BlendShapePreset.OH }, { "ey", BlendShapePreset.E },
            { "eh", BlendShapePreset.E }, { "er", BlendShapePreset.O }, { "y", BlendShapePreset.I },
            { "iy", BlendShapePreset.I }, { "ih", BlendShapePreset.I }, { "w", BlendShapePreset.OU },
            { "uw", BlendShapePreset.OU }, { "uh", BlendShapePreset.OU }, { "ow", BlendShapePreset.OH },
            { "oy", BlendShapePreset.OH }, { "ay", BlendShapePreset.AA }, { "aw", BlendShapePreset.OH },
            { "p", BlendShapePreset.M }, { "b", BlendShapePreset.M }, { "m", BlendShapePreset.M },
            { "f", BlendShapePreset.F }, { "v", BlendShapePreset.F }, { "th", BlendShapePreset.TH },
            { "dh", BlendShapePreset.TH }, { "t", BlendShapePreset.TH }, { "d", BlendShapePreset.TH },
            { "n", BlendShapePreset.N }, { "s", BlendShapePreset.S }, { "z", BlendShapePreset.S },
            { "sh", BlendShapePreset.S }, { "zh", BlendShapePreset.S }, { "ch", BlendShapePreset.S },
            { "jh", BlendShapePreset.S }, { "k", BlendShapePreset.K }, { "g", BlendShapePreset.K },
            { "ng", BlendShapePreset.N }, { "l", BlendShapePreset.N }, { "r", BlendShapePreset.R },
        };

        // Emotion to blendshape mapping
        private readonly Dictionary<string, (BlendShapePreset preset, float intensity)> emotionMap = new Dictionary<string, (BlendShapePreset, float)>
        {
            { "happy", (BlendShapePreset.Happy, 0.8f) },
            { "sad", (BlendShapePreset.Sad, 0.7f) },
            { "angry", (BlendShapePreset.Angry, 0.9f) },
            { "surprised", (BlendShapePreset.Surprised, 0.8f) },
            { "fear", (BlendShapePreset.Fear, 0.7f) },
            { "disgust", (BlendShapePreset.Disgusted, 0.6f) },
            { "neutral", (BlendShapePreset.Neutral, 1.0f) },
            { "curious", (BlendShapePreset.Surprised, 0.5f) },
            { "concerned", (BlendShapePreset.Sad, 0.4f) },
            { "amused", (BlendShapePreset.Happy, 0.6f) },
            { "confident", (BlendShapePreset.Happy, 0.5f) },
        };

        private void Awake()
        {
            if (blendShapeProxy == null)
                blendShapeProxy = GetComponent<VRMBlendShapeProxy>();
            if (animator == null)
                animator = GetComponent<Animator>();

            cancellationTokenSource = new CancellationTokenSource();
        }

        private async void Start()
        {
            await ConnectWithRetryAsync(cancellationTokenSource.Token);
        }

        private async Task ConnectWithRetryAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    await ConnectAsync(token);
                    if (isConnected)
                    {
                        _ = ReceiveLoopAsync(token);
                        _ = SendLoopAsync(token);
                        break;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[SereinAvatar] Connection failed: {ex.Message}. Retrying in {reconnectInterval}s...");
                }

                await Task.Delay(TimeSpan.FromSeconds(reconnectInterval), token);
            }
        }

        private async Task ConnectAsync(CancellationToken token)
        {
            tcpClient = new TcpClient();
            tcpClient.NoDelay = true;
            
            await tcpClient.ConnectAsync(serverHost, serverPort);
            networkStream = tcpClient.GetStream();
            isConnected = true;
            
            Debug.Log($"[SereinAvatar] Connected to {serverHost}:{serverPort}");
            
            // Send hello message
            await SendMessageAsync("{\"type\":\"hello\",\"client\":\"avatar\"}", token);
        }

        private async Task ReceiveLoopAsync(CancellationToken token)
        {
            byte[] buffer = new byte[4096];
            
            while (isConnected && !token.IsCancellationRequested)
            {
                try
                {
                    int bytesRead = await networkStream.ReadAsync(buffer, 0, buffer.Length, token);
                    if (bytesRead == 0) break;

                    string message = Encoding.UTF8.GetString(buffer, 0, bytesRead);
                    
                    // Handle multiple messages in buffer
                    string[] messages = message.Split('\n');
                    foreach (string msg in messages)
                    {
                        if (!string.IsNullOrWhiteSpace(msg))
                            ProcessMessage(msg.Trim());
                    }
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    Debug.LogError($"[SereinAvatar] Receive error: {ex.Message}");
                    break;
                }
            }
            
            Disconnect();
        }

        private async Task SendLoopAsync(CancellationToken token)
        {
            while (isConnected && !token.IsCancellationRequested)
            {
                string message = null;
                lock (queueLock)
                {
                    if (messageQueue.Count > 0)
                        message = messageQueue.Dequeue();
                }

                if (message != null)
                {
                    try
                    {
                        await SendMessageAsync(message, token);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogError($"[SereinAvatar] Send error: {ex.Message}");
                        break;
                    }
                }
                else
                {
                    await Task.Delay(10, token);
                }
            }
        }

        private async Task SendMessageAsync(string message, CancellationToken token)
        {
            if (!isConnected || networkStream == null) return;
            
            byte[] data = Encoding.UTF8.GetBytes(message + "\n");
            await networkStream.WriteAsync(data, 0, data.Length, token);
            await networkStream.FlushAsync(token);
        }

        public void EnqueueMessage(string message)
        {
            lock (queueLock)
            {
                messageQueue.Enqueue(message);
            }
        }

        private void ProcessMessage(string json)
        {
            try
            {
                var message = JsonUtility.FromJson<AvatarMessage>(json);
                
                switch (message.type)
                {
                    case "speak":
                        HandleSpeak(message);
                        break;
                    case "emotion":
                        HandleEmotion(message);
                        break;
                    case "viseme":
                        HandleViseme(message);
                        break;
                    case "expression":
                        HandleExpression(message);
                        break;
                    case "head_pose":
                        HandleHeadPose(message);
                        break;
                    case "eye_gaze":
                        HandleEyeGaze(message);
                        break;
                    case "gesture":
                        HandleGesture(message);
                        break;
                }
            }
            catch (Exception ex)
            {
                Debug.LogError($"[SereinAvatar] Failed to process message: {ex.Message}");
            }
        }

        private void HandleSpeak(AvatarMessage msg)
        {
            if (!string.IsNullOrEmpty(msg.text))
            {
                // Trigger speaking animation
                if (animator != null)
                {
                    animator.SetBool("IsSpeaking", true);
                    animator.SetTrigger("Speak");
                }
                
                // Schedule visemes if provided
                if (msg.visemes != null && msg.visemes.Length > 0)
                {
                    StartCoroutine(PlayVisemes(msg.visemes));
                }
            }
        }

        private void HandleEmotion(AvatarMessage msg)
        {
            if (emotionMap.TryGetValue(msg.emotion, out var mapping))
            {
                blendShapeProxy.AccumulateValue(mapping.preset, mapping.intensity);
                
                // Reset after duration
                StartCoroutine(ResetEmotion(mapping.preset, msg.duration > 0 ? msg.duration : 3f));
            }
        }

        private void HandleViseme(AvatarMessage msg)
        {
            if (!string.IsNullOrEmpty(msg.viseme))
            {
                if (visemeMap.TryGetValue(msg.viseme, out BlendShapePreset preset))
                {
                    float intensity = msg.intensity > 0 ? msg.intensity : 1.0f;
                    blendShapeProxy.AccumulateValue(preset, intensity);
                    StartCoroutine(ResetViseme(preset, 0.1f));
                }
            }
        }

        private void HandleExpression(AvatarMessage msg)
        {
            if (Enum.TryParse<BlendShapePreset>(msg.preset, true, out BlendShapePreset preset))
            {
                float intensity = msg.intensity > 0 ? msg.intensity : 1.0f;
                blendShapeProxy.AccumulateValue(preset, intensity);
            }
        }

        private void HandleHeadPose(AvatarMessage msg)
        {
            if (animator != null)
            {
                animator.SetFloat("HeadYaw", msg.yaw);
                animator.SetFloat("HeadPitch", msg.pitch);
                animator.SetFloat("HeadRoll", msg.roll);
            }
        }

        private void HandleEyeGaze(AvatarMessage msg)
        {
            if (animator != null)
            {
                animator.SetFloat("EyeX", msg.x);
                animator.SetFloat("EyeY", msg.y);
            }
        }

        private void HandleGesture(AvatarMessage msg)
        {
            if (!string.IsNullOrEmpty(msg.gesture))
            {
                var player = GetComponent<SereinPerformancePlayer>();
                if (player != null)
                {
                    player.PlayGesture(msg.gesture, msg.duration > 0 ? msg.duration : 2.0f);
                }
                else if (animator != null)
                {
                    animator.SetTrigger(msg.gesture);
                }
            }
        }

        private System.Collections.IEnumerator PlayVisemes(VisemeData[] visemes)
        {
            foreach (var v in visemes)
            {
                if (visemeMap.TryGetValue(v.name, out BlendShapePreset preset))
                {
                    blendShapeProxy.AccumulateValue(preset, v.intensity);
                }
                yield return new WaitForSeconds(v.duration);
            }
        }

        private System.Collections.IEnumerator ResetViseme(BlendShapePreset preset, float delay)
        {
            yield return new WaitForSeconds(delay);
            blendShapeProxy.AccumulateValue(preset, 0f);
        }

        private System.Collections.IEnumerator ResetEmotion(BlendShapePreset preset, float delay)
        {
            yield return new WaitForSeconds(delay);
            blendShapeProxy.AccumulateValue(preset, 0f);
        }

        private void Disconnect()
        {
            isConnected = false;
            if (animator != null)
                animator.SetBool("IsSpeaking", false);
            
            try { networkStream?.Close(); } catch { }
            try { tcpClient?.Close(); } catch { }
            
            Debug.Log("[SereinAvatar] Disconnected");
            
            if (!cancellationTokenSource.Token.IsCancellationRequested)
            {
                _ = ConnectWithRetryAsync(cancellationTokenSource.Token);
            }
        }

        private void OnDestroy()
        {
            cancellationTokenSource?.Cancel();
            Disconnect();
        }

        [Serializable]
        public class AvatarMessage
        {
            public string type;
            public string text;
            public string emotion;
            public float duration;
            public string viseme;
            public float intensity;
            public string preset;
            public float yaw, pitch, roll;
            public float x, y;
            public string gesture;
            public VisemeData[] visemes;
        }

        [Serializable]
        public class VisemeData
        {
            public string name;
            public float intensity;
            public float duration;
        }
    }
}