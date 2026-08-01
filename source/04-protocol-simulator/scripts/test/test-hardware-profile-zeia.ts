import { hardwareProfileFromZeia } from "../../src/data/labwareCatalog";

function expect(actual: string, expected: string, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${expected}, got ${actual}`);
  }
}

// Exact tube phrases beat ambiguous Carrier.Miscellaneous FG.
expect(
  hardwareProfileFromZeia({
    functionalGroup: "Carrier.Miscellaneous",
    nameText: "1x16 15ml Falcon Tube Runner"
  }),
  "tube-holder",
  "tube runner phrase"
);
expect(
  hardwareProfileFromZeia({
    functionalGroup: "Carrier.Miscellaneous",
    nameText: "3x32 10mm Tube Holder"
  }),
  "tube-holder",
  "tube holder phrase"
);

// FG maps without name invent.
expect(hardwareProfileFromZeia({ functionalGroup: "Labware.FCA DiTi", nameText: "Custom Tips" }), "tip-box", "DiTi FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Labware.Trough", nameText: "Custom Trough" }), "reservoir", "Trough FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Labware.Microplate", nameText: "Custom Plate" }), "plate", "Microplate FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Labware.Deep Well", nameText: "Custom DWP" }), "plate", "Deep Well FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Labware.Wash and Waste", nameText: "Custom Wash" }), "waste", "Wash FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Carrier.Grid Segment", nameText: "Grid" }), "carrier", "Grid FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Carrier.Hotel", nameText: "Hotel" }), "adapter", "Hotel FG");
expect(hardwareProfileFromZeia({ functionalGroup: "Carrier.Device", nameText: "Device" }), "device", "Device FG");

// No keyword invent for filter / DWP / tip / falcon alone.
expect(hardwareProfileFromZeia({ nameText: "24 Filter Plate" }), "generic", "no filter invent");
expect(hardwareProfileFromZeia({ nameText: "24 DWP" }), "generic", "no dwp invent");
expect(hardwareProfileFromZeia({ nameText: "Something with tip in name" }), "generic", "no tip invent");
expect(hardwareProfileFromZeia({ nameText: "50ml Falcon" }), "generic", "no falcon invent");
expect(
  hardwareProfileFromZeia({ functionalGroup: "Carrier.Miscellaneous", nameText: "Random Carrier Bit" }),
  "carrier",
  "misc carrier without tube phrase"
);

console.log("ok hardwareProfileFromZeia FG + exact tube phrases");
