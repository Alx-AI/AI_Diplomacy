import { gameState } from "./gameState";
import { logger } from "./logger";
// --- LOADING & DISPLAYING GAME PHASES ---
export function loadGameBtnFunction(file) {
  const reader = new FileReader();
  reader.onload = e => {
    gameState.loadGameData(e.target?.result)
  };
  reader.onerror = () => {
    logger.log("Error reading file.")
  };
  reader.readAsText(file);
}
export const loadBtn = document.getElementById('load-btn');
export const fileInput = document.getElementById('file-input');
export const prevBtn = document.getElementById('prev-btn');
export const nextBtn = document.getElementById('next-btn');
export const playBtn = document.getElementById('play-btn');
export const speedSelector = document.getElementById('speed-selector');
export const phaseDisplay = document.getElementById('phase-display');
export const mapView = document.getElementById('map-view');
export const leaderboard = document.getElementById('leaderboard');

// Add roundRect polyfill for browsers that don't support it
if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function (x, y, width, height, radius) {
    if (typeof radius === 'undefined') {
      radius = 5;
    }
    this.beginPath();
    this.moveTo(x + radius, y);
    this.lineTo(x + width - radius, y);
    this.arcTo(x + width, y, x + width, y + radius, radius);
    this.lineTo(x + width, y + height - radius);
    this.arcTo(x + width, y + height, x + width - radius, y + height, radius);
    this.lineTo(x + radius, y + height);
    this.arcTo(x, y + height, x, y + height - radius, radius);
    this.lineTo(x, y + radius);
    this.arcTo(x, y, x + radius, y, radius);
    this.closePath();
    return this;
  };
}


