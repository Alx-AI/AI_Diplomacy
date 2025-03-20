import { gameState } from "./gameState";
import { logger } from "./logger";
import { phaseDisplay } from "./domElements";
import { createSupplyCenters, createUnitMesh, initUnits } from "./units/create";
import { updateSupplyCenterOwnership, updateLeaderboard, updateMapOwnership } from "./map/state";
import { updateChatWindows, addToNewsBanner } from "./domElements/chatWindows";
import { createTweenAnimations } from "./units/animate";
import { speakSummary } from "./speech";
import { config } from "./config";

/**
 * Unified function to display a phase with proper transitions
 * Handles both initial display and animated transitions between phases
 * @param skipMessages Whether to skip message animations (used for initial load)
 */
export function displayPhase(skipMessages = false) {
  let index = gameState.phaseIndex
  if (!gameState.gameData || !gameState.gameData.phases ||
    index < 0 || index >= gameState.gameData.phases.length) {
    logger.log("Invalid phase index.");
    return;
  }

  // Handle the special case for the first phase (index 0)
  const isFirstPhase = index === 0;
  const currentPhase = gameState.gameData.phases[index];

  // Only get previous phase if not the first phase
  const prevIndex = isFirstPhase ? null : (index > 0 ? index - 1 : null);
  const previousPhase = prevIndex !== null ? gameState.gameData.phases[prevIndex] : null;

  // Update phase display with smooth transition
  if (phaseDisplay) {
    // Add fade-out effect
    phaseDisplay.style.transition = 'opacity 0.3s ease-out';
    phaseDisplay.style.opacity = '0';

    // Update text after fade-out
    setTimeout(() => {
      phaseDisplay.textContent = `Era: ${currentPhase.name || 'Unknown Era'} (${index + 1}/${gameState.gameData.phases.length})`;
      // Fade back in
      phaseDisplay.style.opacity = '1';
    }, 300);
  }

  // Clear existing units except supply centers
  const supplyCenters = gameState.unitMeshes.filter(m => m.userData && m.userData.isSupplyCenter);
  const oldUnits = gameState.unitMeshes.filter(m => m.userData && !m.userData.isSupplyCenter);

  // Update supply centers
  if (currentPhase.state?.centers) {
    updateSupplyCenterOwnership(currentPhase.state.centers);
  }


  // Update UI elements with smooth transitions
  updateLeaderboard(currentPhase);
  updateMapOwnership(currentPhase);

  // Add phase info to news banner if not already there
  const phaseBannerText = `Phase: ${currentPhase.name}`;
  addToNewsBanner(phaseBannerText);

  // Log phase details to console only, don't update info panel with this
  const phaseInfo = `Phase: ${currentPhase.name}\nSCs: ${currentPhase.state?.centers ? JSON.stringify(currentPhase.state.centers) : 'None'}\nUnits: ${currentPhase.state?.units ? JSON.stringify(currentPhase.state.units) : 'None'}`;
  console.log(phaseInfo); // Use console.log instead of logger.log

  // Update info panel with power information
  logger.updateInfoPanel();

  // Show messages with animation or immediately based on skipMessages flag
  if (!skipMessages) {
    updateChatWindows(currentPhase, true);
  } else {
    gameState.messagesPlaying = false;
  }

  // Only animate if not the first phase and animations are requested
  if (!isFirstPhase && !skipMessages) {
    if (previousPhase) {
      createTweenAnimations(currentPhase, previousPhase);
    }
  } else {
    logger.log("No animations for this phase transition");
    gameState.messagesPlaying = false;
  }
}

/**
 * Display the initial phase without animations
 * Used when first loading a game
 */
export function displayInitialPhase() {
  initUnits();
  gameState.phaseIndex = 0;
  displayPhase(true);
}

/**
 * Display a phase with animations
 * Used during normal gameplay
 */
export function displayPhaseWithAnimation() {
  displayPhase(false);
}

/**
 * Advances to the next phase in the game sequence
 * Handles speaking summaries and transitioning to the next phase
 */
export function advanceToNextPhase() {
  console.log("advanceToNextPhase called");

  if (!gameState.gameData || !gameState.gameData.phases || gameState.phaseIndex < 0) {
    logger.log("Cannot advance phase: invalid game state");
    return;
  }

  // Reset the nextPhaseScheduled flag to allow scheduling the next phase
  gameState.nextPhaseScheduled = false;

  // Get current phase
  const currentPhase = gameState.gameData.phases[gameState.phaseIndex];

  console.log(`Current phase: ${currentPhase.name}, Has summary: ${Boolean(currentPhase.summary)}`);
  if (currentPhase.summary) {
    console.log(`Summary preview: "${currentPhase.summary.substring(0, 50)}..."`);
  }

  if (config.isDebugMode) {
    console.log(`Processing phase transition for ${currentPhase.name}`);
  }

  // First show summary if available
  if (currentPhase.summary && currentPhase.summary.trim() !== '') {
    // Update the news banner with full summary
    addToNewsBanner(`(${currentPhase.name}) ${currentPhase.summary}`);
    console.log("Added summary to news banner, preparing to call speakSummary");

    // Speak the summary and advance after
    speakSummary(currentPhase.summary)
      .then(() => {
        console.log("Speech completed successfully");
        if (gameState.isPlaying) {
          moveToNextPhase();
        }
      })
      .catch((error) => {
        console.error("Speech failed with error:", error);
        if (gameState.isPlaying) {
          moveToNextPhase();
        }
      });
  } else {
    console.log("No summary available, skipping speech");
    // No summary to speak, advance immediately
    moveToNextPhase();
  }
}

/**
 * Internal helper to handle the actual phase advancement
 */
function moveToNextPhase() {
  // Clear any existing animations to prevent overlap
  if (gameState.playbackTimer) {
    clearTimeout(gameState.playbackTimer);
    gameState.playbackTimer = 0;
  }

  // Clear any existing animations
  gameState.unitAnimations = [];

  // Reset animation state
  gameState.isAnimating = false;
  gameState.messagesPlaying = false;

  // Advance the phase index
  if (gameState.gameData && gameState.phaseIndex >= gameState.gameData.phases.length - 1) {
    gameState.phaseIndex = 0;
    logger.log("Reached end of game, looping back to start");
  } else {
    gameState.phaseIndex++;
  }

  if (config.isDebugMode && gameState.gameData) {
    console.log(`Moving to phase ${gameState.gameData.phases[gameState.phaseIndex].name}`);
  }

  // Display the next phase and start showing its messages
  displayPhaseWithAnimation(gameState.phaseIndex);
}
