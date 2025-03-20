import { z } from "zod";

export enum ProvTypeENUM {
  WATER = "Water",
  COAST = "Coast",
  LAND = "Land",
  INPASSABLE = "Inpassable",
}

export enum PowerENUM {
  AUSTRIA = "AUSTRIA",
  ENGLAND = "ENGLAND",
  FRANCE = "FRANCE",
  GERMANY = "GERMANY",
  ITALY = "ITALY",
  RUSSIA = "RUSSIA",
  TURKEY = "TURKEY",
}

export const ProvTypeSchema = z.nativeEnum(ProvTypeENUM);
export const PowerENUMSchema = z.preprocess((arg) => {
  if (typeof arg === "string") {
    // Convert to uppercase to ensure consistent power representation
    return arg.toUpperCase();
  }
  return arg;
}, z.nativeEnum(PowerENUM));

// Where to place the label for the province
export const LabelSchema = z.object({
  x: z.number(),
  y: z.number(),
});

// Representation of where to place the unit on the map for a given province.
export const UnitSchema = z.object({
  x: z.number(),
  y: z.number(),
});

export const ProvinceSchema = z.object({
  label: LabelSchema,
  type: ProvTypeSchema,
  unit: UnitSchema.optional(),
  owner: PowerENUMSchema.optional(),
  isSupplyCenter: z.boolean().optional()
});

export const CoordinateDataSchema = z.object({
  provinces: z.record(z.string(), ProvinceSchema),
});

export type Province = z.infer<typeof ProvinceSchema>;
export type CoordinateData = z.infer<typeof CoordinateDataSchema>;
enum ProvinceENUM {
  ANK = "ANK",
  ARM = "ARM",
  CON = "CON",
  MOS = "MOS",
  SEV = "SEV",
  STP = "STP",
  SYR = "SYR",
  UKR = "UKR",
  LVN = "LVN",
  WAR = "WAR",
  PRU = "PRU",
  SIL = "SIL",
  BER = "BER",
  KIE = "KIE",
  RUH = "RUH",
  MUN = "MUN",
  RUM = "RUM",
  BUL = "BUL",
  GRE = "GRE",
  SMY = "SMY",
  ALB = "ALB",
  SER = "SER",
  BUD = "BUD",
  GAL = "GAL",
  VIE = "VIE",
  BOH = "BOH",
  TYR = "TYR",
  TRI = "TRI",
  FIN = "FIN",
  SWE = "SWE",
  NWY = "NWY",
  DEN = "DEN",
  HOL = "HOL",
  BEL = "BEL",
  SWI = "SWI",
  VEN = "VEN",
  PIE = "PIE",
  TUS = "TUS",
  ROM = "ROM",
  APU = "APU",
  NAP = "NAP",
  BUR = "BUR",
  MAR = "MAR",
  GAS = "GAS",
  PIC = "PIC",
  PAR = "PAR",
  BRE = "BRE",
  SPA = "SPA",
  POR = "POR",
  NAF = "NAF",
  TUN = "TUN",
  LON = "LON",
  WAL = "WAL",
  LVP = "LVP",
  YOR = "YOR",
  EDI = "EDI",
  CLY = "CLY",
  NAT = "NAT",
  NRG = "NRG",
  BAR = "BAR",
  BOT = "BOT",
  BAL = "BAL",
  SKA = "SKA",
  HEL = "HEL",
  NTH = "NTH",
  ENG = "ENG",
  IRI = "IRI",
  MID = "MID",
  WES = "WES",
  GOL = "GOL",
  TYN = "TYN",
  ADR = "ADR",
  ION = "ION",
  AEG = "AEG",
  EAS = "EAS",
  BLA = "BLA",
}

export const ProvinceENUMSchema = z.nativeEnum(ProvinceENUM)
