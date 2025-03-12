import type { GamePhase } from "../types/gameState";
import { createUnitMesh } from "./create";
import { UnitMesh } from "../types/units";
import { getProvincePosition } from "../map/utils";
import { coordinateData } from "../gameState";

//FIXME: Move this to a file with all the constants
let animationDuration = 1500; // Duration of unit movement animation in ms
enum AnimationTypeENUM {
  CREATE,
  MOVE,
  DELETE,
}
export type UnitAnimation = {
  animationType: AnimationTypeENUM
}

export function createAnimationsForPhaseTransition(unitMeshes: UnitMesh[], currentPhase: GamePhase, previousPhase: GamePhase | null): UnitAnimation[] {
  let unitAnimations: UnitAnimation[] = []
  // Prepare unit position maps
  const previousUnitPositions = {};
  if (previousPhase.state?.units) {
    for (const [power, unitArr] of Object.entries(previousPhase.state.units)) {
      unitArr.forEach(unitStr => {
        const match = unitStr.match(/^([AF])\s+(.+)$/);
        if (match) {
          const key = `${power} -${match[1]} -${match[2]} `;
          previousUnitPositions[key] = getProvincePosition(coordinateData, match[2]);
        }
      });
    }
  }

  // Animate new units from old positions (or spawn from below)
  if (currentPhase.state?.units) {
    for (const [power, unitArr] of Object.entries(currentPhase.state.units)) {
      unitArr.forEach(unitStr => {
        // For each unit, create a new mesh
        const match = unitStr.match(/^([AF])\s+(.+)$/);
        if (!match) return;
        const unitType = match[1];
        const location = match[2];


        // Current final
        const currentPos = getProvincePosition(coordinateData, location);

        let startPos;
        let matchFound = false;
        for (const prevKey in previousUnitPositions) {
          if (prevKey.startsWith(`${power} -${unitType} `)) {
            startPos = previousUnitPositions[prevKey];
            matchFound = true;
            delete previousUnitPositions[prevKey];
            break;
          }
        }
        if (!matchFound) {
          // TODO: Add a spawn animation?
          //
          // New spawn
          startPos = { x: currentPos.x, y: -20, z: currentPos.z };
        }

        const unitMesh = createUnitMesh({
          power: power,
          province: location,
          type: unitType,
        });
        unitMesh.position.set(startPos.x, 10, startPos.z);

        // Animate
        unitAnimations.push({
          object: unitMesh,
          startPos,
          endPos: currentPos,
          startTime: Date.now(),
          duration: animationDuration
        });
      });
    }
  }
  return unitAnimations
}
// Easing function for smooth animations
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export function processUnitAnimation(anim: UnitAnimation) {

  const currentTime = Date.now();
  const elapsed = currentTime - anim.startTime;
  // Calculate progress (0 to 1)
  const progress = Math.min(1, elapsed / anim.duration);

  // Apply movement
  if (progress < 1) {
    // Apply easing for more natural movement - ease in and out
    const easedProgress = easeInOutCubic(progress);

    // Update position
    anim.object.position.x = anim.startPos.x + (anim.endPos.x - anim.startPos.x) * easedProgress;
    anim.object.position.z = anim.startPos.z + (anim.endPos.z - anim.startPos.z) * easedProgress;

    // Subtle bobbing up and down during movement
    anim.object.position.y = 10 + Math.sin(progress * Math.PI * 2) * 5;

    // For fleets (ships), add a gentle rocking motion
    if (anim.object.userData.type === 'F') {
      anim.object.rotation.z = Math.sin(progress * Math.PI * 3) * 0.05;
      anim.object.rotation.x = Math.sin(progress * Math.PI * 2) * 0.05;
    }
  } else {

    // Set final position
    anim.object.position.x = anim.endPos.x;
    anim.object.position.z = anim.endPos.z;
    anim.object.position.y = 10; // Reset height

    // Reset rotation for ships
    if (anim.object.userData.type === 'F') {
      anim.object.rotation.z = 0;
      anim.object.rotation.x = 0;
    }
  }
}
