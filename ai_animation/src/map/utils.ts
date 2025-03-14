
function hashStringToPosition(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  const x = (hash % 800) - 400;
  const z = ((hash >> 8) % 800) - 400;
  return { x, y: 0, z };
}

//TODO: Make coordinateData come from gameState
export function getProvincePosition(coordinateData, loc) {
  // Convert e.g. "Spa/sc" to "SPA_SC" if needed
  const normalized = loc.toUpperCase().replace('/', '_');
  const base = normalized.split('_')[0];

  if (coordinateData && coordinateData.provinces) {
    if (coordinateData.provinces[normalized]) {
      return {
        "x": coordinateData.provinces[normalized].label.x,
        "y": 10,
        "z": coordinateData.provinces[normalized].label.y
      };
    }
    if (coordinateData.provinces[base]) {
      return coordinateData.provinces[base].label;
    }
  }

  // Fallback with offset
  const pos = hashStringToPosition(loc);
  return { x: pos.x, y: pos.y, z: pos.z + 100 };
}
