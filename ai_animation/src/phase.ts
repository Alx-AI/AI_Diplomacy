import { gameState } from "./gameState";
import { logger } from "./logger";
import { phaseDisplay } from "./domElements";
import { createSupplyCenters } from "./units/create";
import { createUnitMesh } from "./units/create";
import { updateSupplyCenterOwnership, updateLeaderboard, updateMapOwnership } from "./map/state";
import { updateChatWindows } from "./domElements/chatWindows";
import { createTweenAnimations } from "./units/animate";

// New function to display initial state without messages
export function displayInitialPhase() {
  let index = 0
  if (!gameState.gameData || !gameState.gameData.phases || index < 0 || index >= gameState.gameData.phases.length) {
    logger.log("Invalid phase index.")
    return;
  }

  // Clear any existing units
  const supplyCenters = gameState.unitMeshes.filter(m => m.userData && m.userData.isSupplyCenter);
  const oldUnits = gameState.unitMeshes.filter(m => m.userData && !m.userData.isSupplyCenter);
  oldUnits.forEach(m => gameState.scene.remove(m));
  gameState.unitMeshes = supplyCenters;

  const phase = gameState.gameData.phases[index];
  phaseDisplay.textContent = `Era: ${phase.name || 'Unknown Era'} (${index + 1}/${gameState.gameData.phases.length})`;

  // Show supply centers
  let newSCs = createSupplyCenters();
  newSCs.forEach((sc) => gameState.scene.add(sc))
  if (phase.state?.centers) {
    updateSupplyCenterOwnership(phase.state.centers);
  }

  // Show units
  if (phase.state?.units) {
    for (const [power, unitArr] of Object.entries(phase.state.units)) {
      unitArr.forEach(unitStr => {
        const match = unitStr.match(/^([AF])\s+(.+)$/);
        if (match) {
          let newUnit = createUnitMesh({
            power: power.toUpperCase(),
            type: match[1],
            province: match[2],
          });
          gameState.scene.add(newUnit)
          gameState.unitMeshes.push(newUnit)
        }
      });
    }
  }

  updateLeaderboard(phase);
  updateMapOwnership(phase)

  logger.log(`Phase: ${phase.name}\nSCs: ${phase.state?.centers ? JSON.stringify(phase.state.centers) : 'None'}\nUnits: ${phase.state?.units ? JSON.stringify(phase.state.units) : 'None'}`)

  // Add: Update info panel
  logger.updateInfoPanel();

}

export function displayPhaseWithAnimation(index) {
  if (!gameState.gameData || !gameState.gameData.phases || index < 0 || index >= gameState.gameData.phases.length) {
    logger.log("Invalid phase index.")
    return;
  }

  const prevIndex = index > 0 ? index - 1 : gameState.gameData.phases.length - 1;
  const currentPhase = gameState.gameData.phases[index];
  const previousPhase = gameState.gameData.phases[prevIndex];

  phaseDisplay.textContent = `Era: ${currentPhase.name || 'Unknown Era'} (${index + 1}/${gameState.gameData.phases.length})`;

  // Rebuild supply centers, remove old units

  // First show messages, THEN animate units after
  // First show messages with stepwise animation
  updateChatWindows(currentPhase, true);


  // Ownership
  if (currentPhase.state?.centers) {
    updateSupplyCenterOwnership(currentPhase.state.centers);
  }

  // Update leaderboard
  updateLeaderboard(currentPhase);
  updateMapOwnership(currentPhase)

  createTweenAnimations(currentPhase, previousPhase);
  let msg = `Phase: ${currentPhase.name}\nSCs: ${JSON.stringify(currentPhase.state.centers)} \nUnits: ${currentPhase.state?.units ? JSON.stringify(currentPhase.state.units) : 'None'} `
  // Panel

  // Add: Update info panel
  logger.updateInfoPanel();

}

async function advanceToNextPhase() {
  // Only show a summary if we have at least started the first phase
  // and only if the just-ended phase has a "summary" property.
  if (gameState.gameData && gameState.gameData.phases && gameState.phaseIndex >= 0) {
    const justEndedPhase = gameState.gameData.phases[gameState.phaseIndex];
    if (justEndedPhase.summary && justEndedPhase.summary.trim() !== '') {
      // UPDATED: First update the news banner with full summary
      addToNewsBanner(`(${justEndedPhase.name}) ${justEndedPhase.summary}`);

      // Then speak the summary (will be truncated internally)
      await speakSummary(justEndedPhase.summary);
    }
  }

  // If we've reached the end, loop back to the beginning
  if (gameState.phaseIndex >= gameState.gameData.phases.length - 1) {
    gameState.phaseIndex = 0;
  } else {
    gameState.phaseIndex++;
  }

  // Display the new phase with animation
  displayPhaseWithAnimation(gameState.phaseIndex);
}
