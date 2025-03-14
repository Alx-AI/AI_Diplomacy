
// --- NEW: ElevenLabs TTS helper function ---
const ELEVENLABS_API_KEY = import.meta.env.VITE_ELEVENLABS_API_KEY || "";
const VOICE_ID = "onwK4e9ZLuTAKqWW03F9";
const MODEL_ID = "eleven_multilingual_v2";
/**
 * Call ElevenLabs TTS to speak out loud. Returns a promise that
 * resolves only after the audio finishes playing (or fails).
 * Now accepts only the first 100 characters for brevity.
 */
async function speakSummary(summaryText) {
  if (!ELEVENLABS_API_KEY) {
    console.warn("No ElevenLabs API key found. Skipping TTS.");
    return;
  }

  // Set the speaking flag to block other animations/transitions
  isSpeaking = true;

  try {
    // Truncate text to first 100 characters for ElevenLabs
    const truncatedText = summaryText.substring(0, 100);
    if (truncatedText.length < summaryText.length) {
      console.log(`TTS text truncated from ${summaryText.length} to 100 characters`);
    }

    // Hit ElevenLabs TTS endpoint with the truncated text
    const response = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${VOICE_ID}`, {
      method: "POST",
      headers: {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
      },
      body: JSON.stringify({
        text: truncatedText,
        model_id: MODEL_ID,
        // Optional fine-tuning parameters
        // voice_settings: { stability: 0.3, similarity_boost: 0.8 },
      })
    });

    if (!response.ok) {
      throw new Error(`ElevenLabs TTS error: ${response.statusText}`);
    }

    // Convert response into a playable blob
    const audioBlob = await response.blob();
    const audioUrl = URL.createObjectURL(audioBlob);

    // Play the audio, pause until finished
    return new Promise((resolve, reject) => {
      const audio = new Audio(audioUrl);
      audio.play().then(() => {
        audio.onended = () => {
          // Clear the speaking flag when audio finishes
          isSpeaking = false;
          resolve();
        };
      }).catch(err => {
        console.error("Audio playback error", err);
        // Make sure to clear the flag even if there's an error
        isSpeaking = false;
        reject(err);
      });
    });

  } catch (err) {
    console.error("Failed to generate TTS from ElevenLabs:", err);
    // Make sure to clear the flag if there's any exception
    isSpeaking = false;
  }
}
