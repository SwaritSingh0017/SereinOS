using UnityEditor;
using UnityEngine;
using UnityEditor.Build.Reporting;

namespace Serein.Avatar
{
    public class BuildScript
    {
        public static void BuildAvatar()
        {
            string[] args = System.Environment.GetCommandLineArgs();
            string buildPath = GetArg(args, "-buildPath", "Build/serein-avatar");
            
            BuildPlayerOptions buildOptions = new BuildPlayerOptions
            {
                scenes = GetEnabledScenes(),
                locationPathName = buildPath,
                target = BuildTarget.StandaloneLinux64,
                options = BuildOptions.None
            };
            
            BuildReport report = BuildPipeline.BuildPlayer(buildOptions);
            BuildSummary summary = report.summary;
            
            if (summary.result == BuildResult.Succeeded)
            {
                Debug.Log($"[SereinAvatar] Build succeeded: {summary.totalSize} bytes");
            }
            else
            {
                Debug.LogError($"[SereinAvatar] Build failed: {summary.result}");
                throw new System.Exception($"Build failed: {summary.result}");
            }
        }
        
        private static string[] GetEnabledScenes()
        {
            var scenes = new System.Collections.Generic.List<string>();
            foreach (EditorBuildSettingsScene scene in EditorBuildSettings.scenes)
            {
                if (scene.enabled)
                    scenes.Add(scene.path);
            }
            return scenes.ToArray();
        }
        
        private static string GetArg(string[] args, string name, string defaultValue)
        {
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name)
                    return args[i + 1];
            }
            return defaultValue;
        }
    }
}