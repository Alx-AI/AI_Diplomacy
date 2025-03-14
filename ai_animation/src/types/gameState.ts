import { z } from 'zod';
import { PowerENUMSchema } from './map';
import { OrderFromString } from './unitOrders';
import { ProvinceENUMSchema } from './map';


const PhaseSchema = z.object({
  messages: z.array(z.any()),
  name: z.string(),
  orders: z.record(PowerENUMSchema, z.array(OrderFromString).nullable()),
  results: z.record(z.string(), z.array(z.any())),
  state: z.object({
    units: z.record(PowerENUMSchema, z.array(z.string())),
    centers: z.record(PowerENUMSchema, z.array(ProvinceENUMSchema))
  }),
  year: z.number().optional(),
});

export const GameSchema = z.object({
  map: z.string(),
  id: z.string(),
  phases: z.array(PhaseSchema),
});

export type GamePhase = z.infer<typeof PhaseSchema>;
export type GameSchemaType = z.infer<typeof GameSchema>;
