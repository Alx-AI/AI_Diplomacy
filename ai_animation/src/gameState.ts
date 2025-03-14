import * as THREE from "three"
import { type Province, type CoordinateData, CoordinateDataSchema, PowerENUM } from "./types/map"
import type { GameSchemaType } from "./types/gameState";
import { GameSchema } from "./types/gameState";
import { prevBtn, nextBtn, playBtn, speedSelector, mapView } from "./domElements";
import { createChatWindows } from "./domElements/chatWindows";
import { logger } from "./logger";
import { OrbitControls } from "three/examples/jsm/Addons.js";
import { displayInitialPhase } from "./phase";
import * as TWEEN from "@tweenjs/tween.js";

//FIXME: This whole file is a mess. Need to organize and format
//
// NEW: Add a lock for text-to-speech
export let isSpeaking = false;   // Lock to pause game flow while TTS is active
export let currentPhaseIndex = 0;
enum AvailableMaps {
  STANDARD = "standard"
}
// Move these definitions BEFORE they're used
function getRandomPower(): PowerENUM {
  const values = Object.values(PowerENUM)

  const idx = Math.floor(Math.random() * values.length);
  return values[idx];
}
export const currentPower = getRandomPower();


class GameState {
  boardState: CoordinateData
  gameData: GameSchemaType | null
  phaseIndex: number
  boardName: string

  // state locks
  messagesPlaying: boolean
  isPlaying: boolean
  isSpeaking: boolean
  isAnimating: boolean

  //Scene for three.js
  scene: THREE.Scene

  // camera and controls
  camControls: OrbitControls
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer

  unitMeshes: THREE.Group[]

  // Animations needed for this turn
  unitAnimations: TWEEN.Tween[]

  //
  playbackTimer: number
  constructor(boardName: AvailableMaps) {
    this.phaseIndex = 0
    this.gameData = null
    this.boardName = boardName
    // State locks
    this.isSpeaking = false
    this.isPlaying = false
    this.isAnimating = false
    this.messagesPlaying = false

    this.scene = new THREE.Scene()
    this.unitMeshes = []
    this.unitAnimations = []
  }

  loadGameData = (gameDataString: string): Promise<void> => {
    return new Promise((resolve, reject) => {
      this.gameData = GameSchema.parse(JSON.parse(gameDataString));
      logger.log(`Game data loaded: ${this.gameData.phases?.length || 0} phases found.`)
      this.phaseIndex = 0;
      if (this.gameData.phases?.length) {
        prevBtn.disabled = false;
        nextBtn.disabled = false;
        playBtn.disabled = false;
        speedSelector.disabled = false;

        // Initialize chat windows
        createChatWindows();
        displayInitialPhase()
        resolve()
      } else {
        reject()
      }
    })
  }

  loadBoardState = (): Promise<void> => {
    return new Promise((resolve, reject) => {
      fetch(`./assets/maps/${this.boardName}/coords.json`)
        .then(response => {
          if (!response.ok) {
            throw new Error(`Failed to load coordinates: ${response.status}`);
          }
          return response.json()
        })
        .then((data) => {
          this.boardState = CoordinateDataSchema.parse(data)
          resolve()
        })
        .catch(error => {
          console.error(error);
          throw error
        });
    })
  }
  initScene = () => {
    this.scene.background = new THREE.Color(0x87CEEB);

    // Camera
    this.camera = new THREE.PerspectiveCamera(
      60,
      mapView.clientWidth / mapView.clientHeight,
      1,
      3000
    );
    this.camera.position.set(0, 800, 900); // MODIFIED: Increased z-value to account for map shift
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(mapView.clientWidth, mapView.clientHeight);
    this.renderer.setPixelRatio(window.devicePixelRatio);
    mapView.appendChild(this.renderer.domElement);

    // Controls
    this.camControls = new OrbitControls(this.camera, this.renderer.domElement);
    this.camControls.enableDamping = true;
    this.camControls.dampingFactor = 0.05;
    this.camControls.screenSpacePanning = true;
    this.camControls.minDistance = 100;
    this.camControls.maxDistance = 2000;
    this.camControls.maxPolarAngle = Math.PI / 2; // Limit so you don't flip under the map
    this.camControls.target.set(0, 0, 100); // ADDED: Set control target to new map center
  }
}

export let gameState = new GameState(AvailableMaps.STANDARD);
