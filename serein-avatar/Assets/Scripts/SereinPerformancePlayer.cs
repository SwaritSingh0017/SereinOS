using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using VRM;

namespace Serein.Avatar
{
    public enum AvatarGesture
    {
        Idle,
        Wave,
        Nod,
        ShakeHead,
        TiltHead,
        Thinking,
        Bow,
        Cheer,
        Confused
    }

    [Serializable]
    public class PerformanceSequence
    {
        public string sequenceName;
        public AvatarGesture gesture;
        public string emotion;
        public float duration = 2.0f;
        public float intensity = 1.0f;
    }

    /// <summary>
    /// Coordinates reactive bodily performances, gestures, and expressive sequences
    /// on Serein's 3D avatar during conversation and system events.
    /// </summary>
    public class SereinPerformancePlayer : MonoBehaviour
    {
        [Header("Components")]
        [SerializeField] private Animator animator;
        [SerializeField] private VRMBlendShapeProxy blendShapeProxy;

        [Header("Head & Eye Settings")]
        [SerializeField] private Transform headBone;
        [SerializeField] private Transform neckBone;
        [SerializeField] private float gestureSmoothing = 8f;

        private Coroutine activeGestureCoroutine;
        private Quaternion initialHeadRotation = Quaternion.identity;
        private Quaternion initialNeckRotation = Quaternion.identity;
        private Quaternion targetHeadOffset = Quaternion.identity;
        private bool isPerforming = false;

        private void Awake()
        {
            if (animator == null)
                animator = GetComponent<Animator>();
            if (blendShapeProxy == null)
                blendShapeProxy = GetComponent<VRMBlendShapeProxy>();

            if (animator != null && animator.isHuman)
            {
                if (headBone == null)
                    headBone = animator.GetBoneTransform(HumanBodyBones.Head);
                if (neckBone == null)
                    neckBone = animator.GetBoneTransform(HumanBodyBones.Neck);
            }

            if (headBone != null)
                initialHeadRotation = headBone.localRotation;
            if (neckBone != null)
                initialNeckRotation = neckBone.localRotation;
        }

        private void LateUpdate()
        {
            // Apply procedural head offsets on top of animation clips
            if (headBone != null && isPerforming)
            {
                headBone.localRotation = Quaternion.Slerp(
                    headBone.localRotation,
                    initialHeadRotation * targetHeadOffset,
                    Time.deltaTime * gestureSmoothing
                );
            }
        }

        /// <summary>
        /// Trigger a predefined gesture by name.
        /// </summary>
        public void PlayGesture(string gestureName, float duration = 2.0f)
        {
            if (Enum.TryParse<AvatarGesture>(gestureName, true, out var gesture))
            {
                PlayGesture(gesture, duration);
            }
            else
            {
                Debug.LogWarning($"[SereinPerformancePlayer] Unknown gesture: {gestureName}");
            }
        }

        /// <summary>
        /// Trigger an AvatarGesture with automatic return to idle.
        /// </summary>
        public void PlayGesture(AvatarGesture gesture, float duration = 2.0f)
        {
            if (activeGestureCoroutine != null)
            {
                StopCoroutine(activeGestureCoroutine);
            }
            activeGestureCoroutine = StartCoroutine(ExecuteGesture(gesture, duration));
        }

        private IEnumerator ExecuteGesture(AvatarGesture gesture, float duration)
        {
            isPerforming = true;
            float elapsed = 0f;

            // Trigger animator parameter if present
            if (animator != null)
            {
                animator.SetTrigger(gesture.ToString());
            }

            // Procedural bone micro-movements as fallback or supplement
            while (elapsed < duration)
            {
                float progress = elapsed / duration;
                float sinFactor = Mathf.Sin(progress * Mathf.PI);

                switch (gesture)
                {
                    case AvatarGesture.Nod:
                        // Affirmative nod (pitch forward twice)
                        float nodAngle = Mathf.Sin(progress * Mathf.PI * 4f) * 12f;
                        targetHeadOffset = Quaternion.Euler(Mathf.Max(0, nodAngle), 0, 0);
                        break;

                    case AvatarGesture.ShakeHead:
                        // Negative head shake (yaw left and right twice)
                        float shakeAngle = Mathf.Sin(progress * Mathf.PI * 4f) * 15f;
                        targetHeadOffset = Quaternion.Euler(0, shakeAngle, 0);
                        break;

                    case AvatarGesture.TiltHead:
                    case AvatarGesture.Confused:
                        // Curious head tilt (roll left/right)
                        float tiltAngle = sinFactor * 12f;
                        targetHeadOffset = Quaternion.Euler(2f, 0, tiltAngle);
                        break;

                    case AvatarGesture.Thinking:
                        // Looking upward and slightly tilted
                        targetHeadOffset = Quaternion.Euler(-10f * sinFactor, 8f * sinFactor, 6f * sinFactor);
                        break;

                    case AvatarGesture.Bow:
                        // Respectful small bow
                        targetHeadOffset = Quaternion.Euler(20f * sinFactor, 0, 0);
                        break;

                    default:
                        targetHeadOffset = Quaternion.identity;
                        break;
                }

                elapsed += Time.deltaTime;
                yield return null;
            }

            // Return to neutral
            targetHeadOffset = Quaternion.identity;
            float resetElapsed = 0f;
            while (resetElapsed < 0.3f)
            {
                resetElapsed += Time.deltaTime;
                yield return null;
            }

            isPerforming = false;
            activeGestureCoroutine = null;
        }

        /// <summary>
        /// Plays a full performance sequence combining emotion, gesture, and audio.
        /// </summary>
        public void PlayPerformance(PerformanceSequence sequence)
        {
            if (sequence == null) return;
            PlayGesture(sequence.gesture, sequence.duration);
        }
    }
}
