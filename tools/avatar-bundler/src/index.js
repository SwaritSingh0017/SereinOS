import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

export class SereinAvatarStage {
  constructor(options = {}) {
    this.container = options.container || document.getElementById('avatar-stage');
    this.vrmUrl = options.vrmUrl || '/models/serein.vrm';
    this.currentVrm = null;
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.clock = new THREE.Clock();

    // Natural Motion States
    this.isBlinking = false;
    this.blinkTime = 0;
    this.nextBlinkTime = 2.0 + Math.random() * 3.0;
    this.isSpeaking = false;
    this.speechTime = 0;
    this.speechDuration = 0;

    // Head Gaze Tracking
    this.targetHeadYaw = 0;
    this.targetHeadPitch = 0;
    this.currentHeadYaw = 0;
    this.currentHeadPitch = 0;

    // Emotional State
    this.currentEmotion = 'neutral';
    this.emotionWeights = { happy: 0, neutral: 1, relaxed: 0, surprised: 0, thinking: 0 };

    this.init();
  }

  init() {
    if (!this.container) return;

    const width = this.container.clientWidth || 450;
    const height = this.container.clientHeight || 700;

    // 1. Scene
    this.scene = new THREE.Scene();

    // 2. Camera: Framed nicely for 3/4 to full body view on the glowing pedestal
    this.camera = new THREE.PerspectiveCamera(28.0, width / height, 0.1, 20.0);
    this.camera.position.set(0.0, 1.15, 2.3);
    this.camera.lookAt(new THREE.Vector3(0.0, 1.05, 0.0));

    // 3. Renderer
    this.renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance'
    });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.canvas = this.renderer.domElement;
    this.canvas.className = 'vrm-avatar-canvas';
    this.canvas.style.position = 'absolute';
    this.canvas.style.top = '0';
    this.canvas.style.left = '0';
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';
    this.canvas.style.zIndex = '8';
    this.canvas.style.pointerEvents = 'none';

    this.container.appendChild(this.canvas);

    // 4. Lighting: Cyber Hologram & Studio Key
    const ambientLight = new THREE.AmbientLight(0xffffff, 1.4);
    this.scene.add(ambientLight);

    const keyLight = new THREE.DirectionalLight(0xfffaf0, 1.8);
    keyLight.position.set(0.8, 2.2, 1.6).normalize();
    this.scene.add(keyLight);

    // Cyber Rim Light (Cyan/Neon Blue matching Serein OS theme)
    const rimLight = new THREE.DirectionalLight(0x00e5ff, 2.2);
    rimLight.position.set(-1.2, 1.6, -1.2).normalize();
    this.scene.add(rimLight);

    // Subtle Purple/Magenta Fill Light
    const fillLight = new THREE.DirectionalLight(0xbd93f9, 1.2);
    fillLight.position.set(1.2, 0.8, -1.0).normalize();
    this.scene.add(fillLight);

    // Bottom Neon Pedestal Up-Light
    const pedestalLight = new THREE.PointLight(0x00f0ff, 2.5, 3.5);
    pedestalLight.position.set(0, 0.1, 0.25);
    this.scene.add(pedestalLight);

    // 5. Gaze Interaction: Head tracks cursor smoothly
    window.addEventListener('mousemove', (e) => {
      const normX = (e.clientX / window.innerWidth) * 2 - 1;
      const normY = -(e.clientY / window.innerHeight) * 2 + 1;
      this.targetHeadYaw = normX * 0.22; // max ~12 degrees yaw
      this.targetHeadPitch = normY * 0.15; // max ~8 degrees pitch
    });

    window.addEventListener('resize', () => this.onResize());

    // 6. Load VRM Model
    this.loadVRM(this.vrmUrl);

    // 7. Start Animation Loop
    this.animate();
  }

  loadVRM(url) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));

    loader.load(
      url,
      (gltf) => {
        const vrm = gltf.userData.vrm;
        if (!vrm) {
          console.warn('No VRM instance found in loaded asset');
          return;
        }

        // Rotate VRM 0.x forward if necessary
        VRMUtils.rotateVRM0(vrm);
        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        VRMUtils.removeUnnecessaryJoints(gltf.scene);

        // Position on pedestal (feet at y = 0.08)
        vrm.scene.position.set(0.0, 0.08, 0.0);

        this.currentVrm = vrm;
        this.scene.add(vrm.scene);

        // Hide static placeholder image smoothly
        const img = document.getElementById('avatar-img');
        if (img) {
          img.style.transition = 'opacity 0.6s ease';
          img.style.opacity = '0';
        }

        console.log('Serein 3D VRM Model Loaded Successfully!');
      },
      (progress) => {
        // Progress event
      },
      (error) => {
        console.warn('Could not load VRM directly (falling back to animated 2.5D):', error);
      }
    );
  }

  onResize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
  }

  // --- Real-time Lip Sync & Viseme Expression Control ---
  setViseme(name, weight) {
    if (!this.currentVrm || !this.currentVrm.expressionManager) return;
    const em = this.currentVrm.expressionManager;
    const map = {
      'aa': ['aa', 'A'],
      'ih': ['ih', 'I'],
      'ou': ['ou', 'U'],
      'ee': ['ee', 'E'],
      'oh': ['oh', 'O']
    };
    const all = ['aa', 'ih', 'ou', 'ee', 'oh', 'A', 'I', 'U', 'E', 'O'];
    all.forEach((k) => {
      try { em.setValue(k, 0); } catch (e) {}
    });

    if (map[name]) {
      map[name].forEach((k) => {
        try { em.setValue(k, Math.min(Math.max(weight, 0), 1)); } catch (e) {}
      });
    }
  }

  speak(durationMs = 2500) {
    this.isSpeaking = true;
    this.speechTime = 0;
    this.speechDuration = durationMs / 1000;
  }

  stopSpeaking() {
    this.isSpeaking = false;
    this.setViseme('aa', 0);
  }

  setEmotion(name) {
    this.currentEmotion = name;
  }

  animate() {
    requestAnimationFrame(() => this.animate());

    const delta = this.clock.getDelta();
    const elapsedTime = this.clock.getElapsedTime();

    if (this.currentVrm) {
      // 1. Natural Breathing Motion (chest and spine bone pitch oscillation)
      const breathCycle = Math.sin(elapsedTime * 2.2); // ~20 breaths/min
      const chestBone = this.currentVrm.humanoid?.getNormalizedBoneNode('chest');
      const spineBone = this.currentVrm.humanoid?.getNormalizedBoneNode('spine');
      if (chestBone) {
        chestBone.rotation.x = breathCycle * 0.025;
      }
      if (spineBone) {
        spineBone.rotation.x = breathCycle * 0.012;
      }

      // 2. Realistic Natural Eye Blinking
      this.blinkTime += delta;
      if (this.blinkTime >= this.nextBlinkTime) {
        this.isBlinking = true;
        this.blinkProgress = 0;
        this.blinkTime = 0;
        this.nextBlinkTime = 2.4 + Math.random() * 3.8;
      }

      if (this.isBlinking) {
        this.blinkProgress += delta * 12.0; // blink duration ~150ms
        let blinkWeight = 0;
        if (this.blinkProgress <= 1.0) {
          blinkWeight = Math.sin(this.blinkProgress * Math.PI);
        } else {
          this.isBlinking = false;
          blinkWeight = 0;
        }
        try {
          this.currentVrm.expressionManager?.setValue('blink', blinkWeight);
          this.currentVrm.expressionManager?.setValue('Blink', blinkWeight);
        } catch (e) {}
      }

      // 3. Dynamic Real-Time Lip-Sync while Speaking
      if (this.isSpeaking) {
        this.speechTime += delta;
        if (this.speechTime > this.speechDuration) {
          this.stopSpeaking();
        } else {
          // Dynamic phoneme cycle simulating natural syllable cadence
          const syllableRate = 7.2;
          const cycle = Math.sin(this.speechTime * syllableRate * Math.PI * 2);
          const mouthOpen = Math.max(0, cycle) * (0.65 + Math.random() * 0.3);

          const vowelIndex = Math.floor((this.speechTime * 4.2) % 5);
          const vowels = ['aa', 'ih', 'ou', 'ee', 'oh'];
          const activeVowel = vowels[vowelIndex];

          vowels.forEach((v) => {
            const weight = v === activeVowel ? mouthOpen : 0;
            this.setViseme(v, weight);
          });
        }
      }

      // 4. Smooth Head & Neck Gaze Tracking with Idle Micro-Sway
      this.currentHeadYaw += (this.targetHeadYaw - this.currentHeadYaw) * 0.08;
      this.currentHeadPitch += (this.targetHeadPitch - this.currentHeadPitch) * 0.08;

      const headBone = this.currentVrm.humanoid?.getNormalizedBoneNode('head');
      const neckBone = this.currentVrm.humanoid?.getNormalizedBoneNode('neck');

      // Subtle lifelike head micro-movement
      const idleTilt = Math.sin(elapsedTime * 0.9) * 0.018;
      const thinkingTilt = this.currentEmotion === 'thinking' ? 0.06 : 0.0;

      if (headBone) {
        headBone.rotation.y = this.currentHeadYaw * 0.7;
        headBone.rotation.x = -this.currentHeadPitch * 0.6;
        headBone.rotation.z = idleTilt + thinkingTilt;
      }
      if (neckBone) {
        neckBone.rotation.y = this.currentHeadYaw * 0.3;
        neckBone.rotation.x = -this.currentHeadPitch * 0.3;
      }

      // 5. Emotional Facial Blend
      const em = this.currentVrm.expressionManager;
      if (em) {
        const targetHappy = (this.currentEmotion === 'happy' || this.isSpeaking) ? 0.75 : 0.0;
        const targetRelaxed = this.currentEmotion === 'relaxed' ? 0.6 : 0.0;
        const targetSurprised = this.currentEmotion === 'surprised' ? 0.7 : 0.0;

        this.emotionWeights.happy += (targetHappy - this.emotionWeights.happy) * 0.06;
        this.emotionWeights.relaxed += (targetRelaxed - this.emotionWeights.relaxed) * 0.06;
        this.emotionWeights.surprised += (targetSurprised - this.emotionWeights.surprised) * 0.06;

        try {
          em.setValue('happy', this.emotionWeights.happy);
          em.setValue('Joy', this.emotionWeights.happy);
          em.setValue('relaxed', this.emotionWeights.relaxed);
          em.setValue('Fun', this.emotionWeights.relaxed);
          em.setValue('surprised', this.emotionWeights.surprised);
        } catch (e) {}
      }

      // Update VRM internal components (spring bones, physics, etc.)
      this.currentVrm.update(delta);
    }

    if (this.renderer && this.scene && this.camera) {
      this.renderer.render(this.scene, this.camera);
    }
  }
}

// Global exposure
if (typeof window !== 'undefined') {
  window.SereinAvatarStage = SereinAvatarStage;
}
