import { describe, expect, it } from "vitest";

import {
  buildDeleteAllTransaction,
  buildDeleteTransaction,
  buildInsertTransaction,
  parseTransactionResponse
} from "./wfsTransactions";


const featureType = {
  prefix: "nrw",
  namespaceUri: "https://nrw.local/scenario",
  typeName: "proposed_chargers"
};

describe("WFS-T transaction builders", () => {
  it("builds a WFS 1.0 insert with escaped fields and lon/lat GML coordinates", () => {
    const xml = buildInsertTransaction(
      {
        name: "Professor's <station> & demo",
        chargingPoints: 4,
        powerKw: 150,
        maxPointPowerKw: 75,
        requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3",
        longitude: 7.123456,
        latitude: 51.654321
      },
      featureType
    );

    expect(xml).toContain('service="WFS" version="1.0.0"');
    expect(xml).toContain('xmlns:nrw="https://nrw.local/scenario"');
    expect(xml).toContain("<nrw:proposed_chargers>");
    expect(xml).toContain("Professor&apos;s &lt;station&gt; &amp; demo");
    expect(xml).toContain("<nrw:charging_points>4</nrw:charging_points>");
    expect(xml).toContain("<nrw:power_kw>150</nrw:power_kw>");
    expect(xml).toContain("<nrw:max_point_power_kw>75</nrw:max_point_power_kw>");
    expect(xml).toContain("<nrw:request_id>f6e942dc-faf7-4db6-b332-928ad2cb86e3</nrw:request_id>");
    expect(xml).toContain("<gml:coordinates>7.123456,51.654321</gml:coordinates>");
    expect(xml).not.toContain("Professor's <station>");
  });

  it("rejects invalid insert values before sending XML", () => {
    expect(() => buildInsertTransaction({
      name: " ", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22, requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7, latitude: 51
    }, featureType)).toThrow("name");
    expect(() => buildInsertTransaction({
      name: "Bad points", chargingPoints: 0, powerKw: 22, maxPointPowerKw: 22, requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7, latitude: 51
    }, featureType)).toThrow("charging points");
    expect(() => buildInsertTransaction({
      name: "Bad power", chargingPoints: 2, powerKw: 1001, maxPointPowerKw: 22, requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7, latitude: 51
    }, featureType)).toThrow("power");
    expect(() => buildInsertTransaction({
      name: "Bad coordinate", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22, requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 181, latitude: 51
    }, featureType)).toThrow("coordinates");
  });

  it("rejects a maximum above total power and a non-UUID request identifier", () => {
    expect(() => buildInsertTransaction({
      name: "Bad maximum", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 50,
      requestId: "f6e942dc-faf7-4db6-b332-928ad2cb86e3", longitude: 7, latitude: 51
    }, featureType)).toThrow("maximum point power");
    expect(() => buildInsertTransaction({
      name: "Bad request", chargingPoints: 2, powerKw: 22, maxPointPowerKw: 22,
      requestId: "not-a-uuid", longitude: 7, latitude: 51
    }, featureType)).toThrow("request identifier");
  });

  it("builds a UUID-filtered delete and rejects XML injection in ids", () => {
    const id = "f6e942dc-faf7-4db6-b332-928ad2cb86e3";
    const xml = buildDeleteTransaction(id, featureType);

    expect(xml).toContain('<wfs:Delete typeName="nrw:proposed_chargers">');
    expect(xml).toContain("<ogc:PropertyName>id</ogc:PropertyName>");
    expect(xml).toContain(`<ogc:Literal>${id}</ogc:Literal>`);
    expect(() => buildDeleteTransaction("x</ogc:Literal>", featureType)).toThrow("UUID");
  });

  it("builds a reset transaction constrained to proposed rows", () => {
    const xml = buildDeleteAllTransaction(featureType);

    expect(xml).toContain("<ogc:PropertyName>status</ogc:PropertyName>");
    expect(xml).toContain("<ogc:Literal>proposed</ogc:Literal>");
  });
});

describe("WFS-T transaction response parsing", () => {
  it("extracts inserted feature ids and transaction counts", () => {
    const result = parseTransactionResponse(`
      <wfs:WFS_TransactionResponse xmlns:wfs="http://www.opengis.net/wfs"
        xmlns:ogc="http://www.opengis.net/ogc">
        <wfs:InsertResult><ogc:FeatureId fid="proposed_chargers.f6e942dc-faf7-4db6-b332-928ad2cb86e3"/></wfs:InsertResult>
        <wfs:TransactionResult><wfs:Status><wfs:SUCCESS/></wfs:Status></wfs:TransactionResult>
      </wfs:WFS_TransactionResponse>
    `);

    expect(result.ok).toBe(true);
    expect(result.insertedFeatureId).toBe("proposed_chargers.f6e942dc-faf7-4db6-b332-928ad2cb86e3");
  });

  it("reads WFS 2 transaction totals", () => {
    const result = parseTransactionResponse(`
      <wfs:TransactionResponse xmlns:wfs="http://www.opengis.net/wfs/2.0">
        <wfs:TransactionSummary>
          <wfs:totalInserted>1</wfs:totalInserted>
          <wfs:totalUpdated>0</wfs:totalUpdated>
          <wfs:totalDeleted>0</wfs:totalDeleted>
        </wfs:TransactionSummary>
      </wfs:TransactionResponse>
    `);

    expect(result).toMatchObject({ ok: true, totalInserted: 1, totalDeleted: 0 });
  });

  it("returns the GeoServer exception text instead of treating HTTP XML as success", () => {
    const result = parseTransactionResponse(`
      <ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows">
        <ows:Exception><ows:ExceptionText>Write access denied</ows:ExceptionText></ows:Exception>
      </ows:ExceptionReport>
    `);

    expect(result).toEqual({ ok: false, error: "Write access denied", errorKind: "exception" });
  });

  it("rejects an unrecognized response", () => {
    expect(parseTransactionResponse("<html>proxy error</html>")).toEqual({
      ok: false,
      error: "GeoServer returned an unrecognized transaction response",
      errorKind: "unrecognized"
    });
  });
});
