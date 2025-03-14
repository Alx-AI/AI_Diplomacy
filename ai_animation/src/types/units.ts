import * as THREE from "three"
import { PowerENUM } from "./map"


export enum UnitTypeENUM {
  A = "A",
  F = "Fleet"
}

export type UnitData = {
  province: string
  power: PowerENUM
  type: UnitTypeENUM
}

export type UnitMesh = {
  mesh?: THREE.Group
  userData: {
    province: string
    isSupplyCenter: boolean
    power: PowerENUM
    starMesh?: THREE.Mesh
    glowMesh: THREE.Mesh
  }
}
