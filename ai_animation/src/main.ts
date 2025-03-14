import * as THREE from "three";
import "./style.css"
import { initMap } from "./map/create";
import { createTweenAnimations } from "./units/animate";
import * as TWEEN from "@tweenjs/tween.js";
import { gameState } from "./gameState";
import { logger } from "./logger";
import { loadBtn, prevBtn, nextBtn, speedSelector, fileInput, playBtn, mapView, loadGameBtnFunction } from "./domElements";
import { updateChatWindows } from "./domElements/chatWindows";
import { displayPhaseWithAnimation } from "./phase";
import { config } from "./config";

//TODO: Create a function that finds a suitable unit location within a given polygon, for placing units better 
//  Currently the location for label, unit, and SC are all the same manually picked location

//const isDebugMode = process.env.NODE_ENV === 'development' || localStorage.getItem('debug') === 'true';
const isDebugMode = config.isDebugMode;

// --- CORE VARIABLES ---

let cameraPanTime = 0;   // Timer that drives the camera panning
const cameraPanSpeed = 0.0005; // Smaller = slower

// --- INITIALIZE SCENE ---
function initScene() {
  gameState.initScene()

  // Lighting (keep it simple)
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
  gameState.scene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
  dirLight.position.set(300, 400, 300);
  gameState.scene.add(dirLight);

  // Load coordinate data, then build the map
  gameState.loadBoardState().then(() => {
    initMap(gameState.scene).then(() => {
      // Load default game file if in debug mode
      if (isDebugMode) {
        loadDefaultGameFile();
      }
    })
  }).catch(err => {
    console.error("Error loading coordinates:", err);
    logger.log(`Error loading coords: ${err.message}`)
  });

  // Handle resizing
  window.addEventListener('resize', onWindowResize);
  // Kick off animation loop
  animate();

  // Initialize info panel
  logger.updateInfoPanel();

}

// --- ANIMATION LOOP ---
function animate() {
  requestAnimationFrame(animate);

  if (gameState.isPlaying) {
    // Pan camera slowly in playback mode
    cameraPanTime += cameraPanSpeed;
    const angle = 0.9 * Math.sin(cameraPanTime) + 1.2;
    const radius = 1300;
    gameState.camera.position.set(
      radius * Math.cos(angle),
      650 + 80 * Math.sin(cameraPanTime * 0.5),
      100 + radius * Math.sin(angle)
    );

    // If messages are done playing but we haven't started unit animations yet
    if (!gameState.messagesPlaying && !gameState.isSpeaking && gameState.unitAnimations.length === 0 && gameState.isPlaying) {
      if (gameState.gameData && gameState.gameData.phases) {
        const prevIndex = gameState.phaseIndex > 0 ? gameState.phaseIndex - 1 : gameState.gameData.phases.length - 1;
        createTweenAnimations(
          gameState.gameData.phases[gameState.phaseIndex],
          gameState.gameData.phases[prevIndex]
        );
      }
    }
  } else {
    gameState.camControls.update();
  }

  // Process unit movement animations using TWEEN.js update

  // Check if all animations are complete
  if (gameState.unitAnimations.length > 0) {
    // Filter out completed animations
    gameState.unitAnimations = gameState.unitAnimations.filter(anim => anim.isPlaying());
    gameState.unitAnimations.forEach((anim) => anim.update())

    // If all animations are complete and we're in playback mode
    if (gameState.unitAnimations.length === 0 && gameState.isPlaying && !gameState.messagesPlaying) {
      // Schedule next phase after a pause delay
      gameState.playbackTimer = setTimeout(() => advanceToNextPhase(), config.playbackSpeed);
    }
  }

  // Update any pulsing or wave animations on supply centers or units
  if (gameState.scene.userData.animatedObjects) {
    gameState.scene.userData.animatedObjects.forEach(obj => {
      if (obj.userData.pulseAnimation) {
        const anim = obj.userData.pulseAnimation;
        anim.time += anim.speed;
        if (obj.userData.glowMesh) {
          const pulseValue = Math.sin(anim.time) * anim.intensity + 0.5;
          obj.userData.glowMesh.material.opacity = 0.2 + (pulseValue * 0.3);
          obj.userData.glowMesh.scale.set(
            1 + (pulseValue * 0.1),
            1 + (pulseValue * 0.1),
            1 + (pulseValue * 0.1)
          );
        }
        // Subtle bobbing up/down
        obj.position.y = 2 + Math.sin(anim.time) * 0.5;
      }
    });
  }

  gameState.camControls.update();
  gameState.renderer.render(gameState.scene, gameState.camera);
}


// --- RESIZE HANDLER ---
function onWindowResize() {
  gameState.camera.aspect = mapView.clientWidth / mapView.clientHeight;
  gameState.camera.updateProjectionMatrix();
  gameState.renderer.setSize(mapView.clientWidth, mapView.clientHeight);
}

// Load a default game if we're running debug
function loadDefaultGameFile() {
  console.log("Loading default game file for debug mode...");

  // Path to the default game file
  const defaultGameFilePath = './assets/default_game.json';

  fetch(defaultGameFilePath)
    .then(response => {
      if (!response.ok) {
        throw new Error(`Failed to load default game file: ${response.status}`);
      }
      return response.text();
    })
    .then(data => {
      gameState.loadGameData(data);
      console.log("Default game file loaded successfully");
    })
    .catch(error => {
      console.error("Error loading default game file:", error);
      logger.log(`Error loading default game: ${error.message}`)
    });
}


// --- PLAYBACK CONTROLS ---
function togglePlayback() {
  if (!gameState.gameData || gameState.gameData.phases.length <= 1) return;

  // NEW: If we're speaking, don't allow toggling playback
  if (gameState.isSpeaking) return;

  gameState.isPlaying = !gameState.isPlaying;

  if (gameState.isPlaying) {
    playBtn.textContent = "⏸ Pause";
    prevBtn.disabled = true;
    nextBtn.disabled = true;

    // First, show the messages of the current phase if it's the initial playback
    const phase = gameState.gameData.phases[gameState.phaseIndex];
    if (phase.messages && phase.messages.length) {
      // Show messages with stepwise animation
      updateChatWindows(phase, true);
    } else {
      // No messages, go straight to unit animations
      displayPhaseWithAnimation(gameState.phaseIndex);
    }
  } else {
    playBtn.textContent = "▶ Play";
    if (gameState.playbackTimer) {
      clearTimeout(gameState.playbackTimer);
      gameState.playbackTimer = null;
    }
    gameState.messagesPlaying = false;
    prevBtn.disabled = false;
    nextBtn.disabled = false;
  }
}



// --- EVENT HANDLERS ---
loadBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) {
    loadGameBtnFunction(file);
  }
});

prevBtn.addEventListener('click', () => {
  if (gameState.phaseIndex > 0) {
    gameState.phaseIndex--;
    displayPhaseWithAnimation(gameState.phaseIndex);
  }
});
nextBtn.addEventListener('click', () => {
  if (gameState.gameData && gameState.phaseIndex < gameState.gameData.phases.length - 1) {
    gameState.phaseIndex++;
    displayPhaseWithAnimation(gameState.phaseIndex);
  }
});

playBtn.addEventListener('click', togglePlayback);

speedSelector.addEventListener('change', e => {
  config.playbackSpeed = parseInt(e.target.value);
  // If we're currently playing, restart the timer with the new speed
  if (gameState.isPlaying && gameState.playbackTimer) {
    clearTimeout(gameState.playbackTimer);
    gameState.playbackTimer = setTimeout(() => advanceToNextPhase(), config.playbackSpeed);
  }
});

// --- BOOTSTRAP ON PAGE LOAD ---
window.addEventListener('load', initScene);



