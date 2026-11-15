/** The letter a direction with no name goes by, taken from its GTFS direction_id
 *  so one id has one name on every screen and in every refetch. */
export function directionLetter(directionId: number): string {
  return String.fromCharCode(65 + directionId);
}
