import { gameState, currentPower } from "./gameState";
import { currentPhaseIndex } from "./gameState";

class Logger {
  get infoPanel() {
    let _panel = document.getElementById('info-panel');
    if (_panel === null) {
      throw new Error("Unable to find the element with id 'info-panel'")
    }
    return _panel
  }
  log = (msg: string) => {
    if (typeof msg !== "string") {
      throw new Error(`Logger messages must be strings, you passed a ${typeof msg}`)
    }
    this.infoPanel.textContent = msg;

    console.log(msg)
  }
  // New function to update info panel with useful information
  updateInfoPanel = () => {
    const totalPhases = gameState.gameData?.phases?.length || 0;
    const currentPhaseNumber = currentPhaseIndex + 1;
    const phaseName = gameState.gameData?.phases?.[currentPhaseIndex]?.name || 'Unknown';

    this.infoPanel.innerHTML = `
    <div><strong>Power:</strong> <span class="power-${currentPower.toLowerCase()}">${currentPower}</span></div>
    <div><strong>Current Phase:</strong> ${phaseName} (${currentPhaseNumber}/${totalPhases})</div>
    <hr/>
    <h4>All-Time Leaderboard</h4>
    <ul style="list-style:none;padding-left:0;margin:0;">
      <li>Austria: 0</li>
      <li>England: 0</li>
      <li>France: 0</li>
      <li>Germany: 0</li>
      <li>Italy: 0</li>
      <li>Russia: 0</li>
      <li>Turkey: 0</li>
    </ul>
  `;
  }
}
export const logger = new Logger()
