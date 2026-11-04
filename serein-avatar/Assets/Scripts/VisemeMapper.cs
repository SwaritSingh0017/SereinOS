// VisemeMapper.cs - Maps phonemes/visemes from TTS to VRM blend shapes

using System;
using System.Collections.Generic;
using UnityEngine;

namespace SereinOS
{
    /// <summary>
    /// Maps TTS phoneme/viseme output to VRM blend shape weights.
    /// Supports Azure Neural TTS (22 viseme IDs) and HeadTTS (Oculus visemes).
    /// </summary>
    public static class VisemeMapper
    {
        // Azure Neural TTS viseme IDs (22 total)
        // 0: sil, 1: pp, 2: ff, 3: th, 4: dd, 5: kk, 6: ch, 7: ss, 8: nn, 9: rr
        // 10: aa, 11: e, 12: ih, 13: oh, 14: ou, 15: rr (rhotic), 16: ll, 17: ww, 18: yy, 19: hh, 20: zh, 21: sh
        
        // Oculus VRM visemes (15 standard)
        // sil, pp, ff, th, dd, kk, ch, ss, nn, rr, aa, e, ih, oh, ou
        
        // VRM Blend Shape names (standard)
        public const string BLEND_SHAPE_AA = "aa";
        public const string BLEND_SHAPE_IH = "ih";
        public const string BLEND_SHAPE_E = "e";
        public const string BLEND_SHAPE_OH = "oh";
        public const string BLEND_SHAPE_OU = "ou";
        public const string BLEND_SHAPE_PP = "pp";
        public const string BLEND_SHAPE_FF = "ff";
        public const string BLEND_SHAPE_TH = "th";
        public const string BLEND_SHAPE_DD = "dd";
        public const string BLEND_SHAPE_KK = "kk";
        public const string BLEND_SHAPE_CH = "ch";
        public const string BLEND_SHAPE_SS = "ss";
        public const string BLEND_SHAPE_NN = "nn";
        public const string BLEND_SHAPE_RR = "rr";
        public const string BLEND_SHAPE_SIL = "sil";
        
        /// <summary>
        /// Maps Azure Neural TTS viseme ID to VRM blend shape weights.
        /// </summary>
        public static Dictionary<string, float> MapAzureViseme(int visemeId, float intensity = 1f)
        {
            var weights = new Dictionary<string, float>();
            
            // Initialize all to 0
            foreach (var name in AllBlendShapes())
            {
                weights[name] = 0f;
            }
            
            // Azure viseme to VRM mapping
            switch (visemeId)
            {
                case 0: // sil - silence
                    weights[BLEND_SHAPE_SIL] = intensity;
                    break;
                    
                case 1: // pp - p, b, m
                    weights[BLEND_SHAPE_PP] = intensity;
                    break;
                    
                case 2: // ff - f, v
                    weights[BLEND_SHAPE_FF] = intensity;
                    break;
                    
                case 3: // th - th, dh
                    weights[BLEND_SHAPE_TH] = intensity;
                    break;
                    
                case 4: // dd - t, d, n, l, s, z
                    weights[BLEND_SHAPE_DD] = intensity;
                    break;
                    
                case 5: // kk - k, g, ng
                    weights[BLEND_SHAPE_KK] = intensity;
                    break;
                    
                case 6: // ch - ch, jh, sh, zh
                    weights[BLEND_SHAPE_CH] = intensity;
                    break;
                    
                case 7: // ss - s, z
                    weights[BLEND_SHAPE_SS] = intensity;
                    break;
                    
                case 8: // nn - n, ng
                    weights[BLEND_SHAPE_NN] = intensity;
                    break;
                    
                case 9: // rr - r
                    weights[BLEND_SHAPE_RR] = intensity;
                    break;
                    
                case 10: // aa - aa, ae, ah
                    weights[BLEND_SHAPE_AA] = intensity;
                    break;
                    
                case 11: // e - eh, ey
                    weights[BLEND_SHAPE_E] = intensity;
                    break;
                    
                case 12: // ih - ih, iy
                    weights[BLEND_SHAPE_IH] = intensity;
                    break;
                    
                case 13: // oh - ao, ow
                    weights[BLEND_SHAPE_OH] = intensity;
                    break;
                    
                case 14: // ou - uw, uh
                    weights[BLEND_SHAPE_OU] = intensity;
                    break;
                    
                // Extended Azure visemes (15-21)
                case 15: // rr (rhotic)
                    weights[BLEND_SHAPE_RR] = intensity * 0.8f;
                    weights[BLEND_SHAPE_AA] = intensity * 0.3f;
                    break;
                    
                case 16: // ll
                    weights[BLEND_SHAPE_DD] = intensity * 0.7f;
                    weights[BLEND_SHAPE_AA] = intensity * 0.2f;
                    break;
                    
                case 17: // ww
                    weights[BLEND_SHAPE_OU] = intensity * 0.8f;
                    weights[BLEND_SHAPE_PP] = intensity * 0.3f;
                    break;
                    
                case 18: // yy
                    weights[BLEND_SHAPE_IH] = intensity * 0.8f;
                    weights[BLEND_SHAPE_E] = intensity * 0.3f;
                    break;
                    
                case 19: // hh
                    weights[BLEND_SHAPE_AA] = intensity * 0.5f;
                    break;
                    
                case 20: // zh
                    weights[BLEND_SHAPE_CH] = intensity * 0.8f;
                    weights[BLEND_SHAPE_SS] = intensity * 0.3f;
                    break;
                    
                case 21: // sh
                    weights[BLEND_SHAPE_CH] = intensity * 0.9f;
                    weights[BLEND_SHAPE_SS] = intensity * 0.2f;
                    break;
            }
            
            return weights;
        }
        
        /// <summary>
        /// Maps HeadTTS/Oculus viseme to VRM blend shape weights.
        /// </summary>
        public static Dictionary<string, float> MapOculusViseme(string visemeName, float intensity = 1f)
        {
            var weights = new Dictionary<string, float>();
            
            foreach (var name in AllBlendShapes())
            {
                weights[name] = 0f;
            }
            
            // Oculus viseme names to VRM blend shapes
            switch (visemeName.ToLower())
            {
                case "sil":
                    weights[BLEND_SHAPE_SIL] = intensity;
                    break;
                case "pp":
                    weights[BLEND_SHAPE_PP] = intensity;
                    break;
                case "ff":
                    weights[BLEND_SHAPE_FF] = intensity;
                    break;
                case "th":
                    weights[BLEND_SHAPE_TH] = intensity;
                    break;
                case "dd":
                    weights[BLEND_SHAPE_DD] = intensity;
                    break;
                case "kk":
                    weights[BLEND_SHAPE_KK] = intensity;
                    break;
                case "ch":
                    weights[BLEND_SHAPE_CH] = intensity;
                    break;
                case "ss":
                    weights[BLEND_SHAPE_SS] = intensity;
                    break;
                case "nn":
                    weights[BLEND_SHAPE_NN] = intensity;
                    break;
                case "rr":
                    weights[BLEND_SHAPE_RR] = intensity;
                    break;
                case "aa":
                    weights[BLEND_SHAPE_AA] = intensity;
                    break;
                case "e":
                    weights[BLEND_SHAPE_E] = intensity;
                    break;
                case "ih":
                    weights[BLEND_SHAPE_IH] = intensity;
                    break;
                case "oh":
                    weights[BLEND_SHAPE_OH] = intensity;
                    break;
                case "ou":
                    weights[BLEND_SHAPE_OU] = intensity;
                    break;
            }
            
            return weights;
        }
        
        /// <summary>
        /// Maps phoneme string to viseme weights (for custom TTS).
        /// </summary>
        public static Dictionary<string, float> MapPhoneme(string phoneme, float intensity = 1f)
        {
            // ARPABET to viseme mapping
            var phonemeToViseme = new Dictionary<string, int>
            {
                // Vowels
                ["AA"] = 10, ["AE"] = 10, ["AH"] = 10, ["AO"] = 13, ["AW"] = 14,
                ["AY"] = 10, ["EH"] = 11, ["ER"] = 15, ["EY"] = 11, ["IH"] = 12,
                ["IY"] = 11, ["OW"] = 13, ["OY"] = 13, ["UH"] = 14, ["UW"] = 14,
                
                // Consonants
                ["P"] = 1, ["B"] = 1, ["M"] = 1,  // pp
                ["F"] = 2, ["V"] = 2,              // ff
                ["TH"] = 3, ["DH"] = 3,            // th
                ["T"] = 4, ["D"] = 4, ["N"] = 8, ["L"] = 16, ["S"] = 7, ["Z"] = 7, // dd/ss
                ["K"] = 5, ["G"] = 5, ["NG"] = 8,  // kk/nn
                ["CH"] = 6, ["JH"] = 6, ["SH"] = 21, ["ZH"] = 20, // ch
                ["R"] = 9,                          // rr
                ["W"] = 17,                         // ww
                ["Y"] = 18,                         // yy
                ["H"] = 19,                         // hh
            };
            
            if (phonemeToViseme.TryGetValue(phoneme.ToUpper(), out int visemeId))
            {
                return MapAzureViseme(visemeId, intensity);
            }
            
            return new Dictionary<string, float>();
        }
        
        public static IEnumerable<string> AllBlendShapes()
        {
            yield return BLEND_SHAPE_AA;
            yield return BLEND_SHAPE_IH;
            yield return BLEND_SHAPE_E;
            yield return BLEND_SHAPE_OH;
            yield return BLEND_SHAPE_OU;
            yield return BLEND_SHAPE_PP;
            yield return BLEND_SHAPE_FF;
            yield return BLEND_SHAPE_TH;
            yield return BLEND_SHAPE_DD;
            yield return BLEND_SHAPE_KK;
            yield return BLEND_SHAPE_CH;
            yield return BLEND_SHAPE_SS;
            yield return BLEND_SHAPE_NN;
            yield return BLEND_SHAPE_RR;
            yield return BLEND_SHAPE_SIL;
        }
        
        /// <summary>
        /// Creates a smooth interpolation between two viseme weight dictionaries.
        /// </summary>
        public static Dictionary<string, float> InterpolateVisemes(
            Dictionary<string, float> from, 
            Dictionary<string, float> to, 
            float t)
        {
            var result = new Dictionary<string, float>();
            float smoothT = t * t * (3f - 2f * t); // Smoothstep
            
            foreach (var key in AllBlendShapes())
            {
                float fromVal = from.GetValueOrDefault(key, 0f);
                float toVal = to.GetValueOrDefault(key, 0f);
                result[key] = Mathf.Lerp(fromVal, toVal, smoothT);
            }
            
            return result;
        }
    }
}