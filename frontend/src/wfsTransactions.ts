export type ProposedChargerInput = {
  name: string;
  chargingPoints: number;
  powerKw: number;
  maxPointPowerKw: number;
  requestId: string;
  longitude: number;
  latitude: number;
};

export type WfsFeatureType = {
  prefix: string;
  namespaceUri: string;
  typeName: string;
  geometryName?: string;
};

export type WfsTransactionResult = {
  ok: boolean;
  error?: string;
  insertedFeatureId?: string;
  insertedFeatureIds?: string[];
  totalInserted?: number;
  totalUpdated?: number;
  totalDeleted?: number;
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const XML_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

function escapeXml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&apos;",
    '"': "&quot;"
  })[character]!);
}

function decodeXml(value: string): string {
  return value.replace(/&(amp|lt|gt|apos|quot);/g, (_entity, name: string) => ({
    amp: "&",
    lt: "<",
    gt: ">",
    apos: "'",
    quot: '"'
  })[name]!);
}

function validateFeatureType(featureType: WfsFeatureType): Required<WfsFeatureType> {
  const geometryName = featureType.geometryName ?? "geom";
  if (!XML_NAME_PATTERN.test(featureType.prefix)) throw new Error("Invalid WFS feature prefix");
  if (!XML_NAME_PATTERN.test(featureType.typeName)) throw new Error("Invalid WFS feature type name");
  if (!XML_NAME_PATTERN.test(geometryName)) throw new Error("Invalid WFS geometry property name");
  if (!featureType.namespaceUri.trim()) throw new Error("WFS namespace URI is required");
  return { ...featureType, geometryName };
}

function transactionStart(featureType: Required<WfsFeatureType>): string {
  return `<wfs:Transaction service="WFS" version="1.0.0"`
    + ` xmlns:wfs="http://www.opengis.net/wfs"`
    + ` xmlns:gml="http://www.opengis.net/gml"`
    + ` xmlns:ogc="http://www.opengis.net/ogc"`
    + ` xmlns:${featureType.prefix}="${escapeXml(featureType.namespaceUri)}">`;
}

function validateInput(input: ProposedChargerInput): ProposedChargerInput {
  const name = input.name.trim();
  if (name.length < 1 || name.length > 120) {
    throw new Error("Proposed charger name must contain 1 to 120 characters");
  }
  if (!Number.isInteger(input.chargingPoints)
      || input.chargingPoints < 1
      || input.chargingPoints > 100) {
    throw new Error("Proposed charger charging points must be an integer from 1 to 100");
  }
  if (!Number.isFinite(input.powerKw) || input.powerKw < 1 || input.powerKw > 1000) {
    throw new Error("Proposed charger power must be from 1 to 1000 kW");
  }
  if (!Number.isFinite(input.maxPointPowerKw)
      || input.maxPointPowerKw < 1
      || input.maxPointPowerKw > input.powerKw) {
    throw new Error("Proposed charger maximum point power must be from 1 kW up to total station power");
  }
  if (!UUID_PATTERN.test(input.requestId)) {
    throw new Error("Proposed charger request identifier must be a UUID");
  }
  if (!Number.isFinite(input.longitude)
      || !Number.isFinite(input.latitude)
      || input.longitude < -180
      || input.longitude > 180
      || input.latitude < -90
      || input.latitude > 90) {
    throw new Error("Proposed charger coordinates must be finite WGS84 longitude/latitude values");
  }
  return { ...input, name };
}

export function buildInsertTransaction(
  uncheckedInput: ProposedChargerInput,
  uncheckedFeatureType: WfsFeatureType
): string {
  const input = validateInput(uncheckedInput);
  const featureType = validateFeatureType(uncheckedFeatureType);
  const prefix = featureType.prefix;
  return `${transactionStart(featureType)}
  <wfs:Insert>
    <${prefix}:${featureType.typeName}>
      <${prefix}:name>${escapeXml(input.name)}</${prefix}:name>
      <${prefix}:charging_points>${input.chargingPoints}</${prefix}:charging_points>
      <${prefix}:power_kw>${input.powerKw}</${prefix}:power_kw>
      <${prefix}:max_point_power_kw>${input.maxPointPowerKw}</${prefix}:max_point_power_kw>
      <${prefix}:request_id>${input.requestId}</${prefix}:request_id>
      <${prefix}:${featureType.geometryName}>
        <gml:Point srsName="http://www.opengis.net/gml/srs/epsg.xml#4326">
          <gml:coordinates>${input.longitude},${input.latitude}</gml:coordinates>
        </gml:Point>
      </${prefix}:${featureType.geometryName}>
    </${prefix}:${featureType.typeName}>
  </wfs:Insert>
</wfs:Transaction>`;
}

export function buildDeleteTransaction(
  id: string,
  uncheckedFeatureType: WfsFeatureType
): string {
  if (!UUID_PATTERN.test(id)) throw new Error("Proposed charger id must be a UUID");
  const featureType = validateFeatureType(uncheckedFeatureType);
  return `${transactionStart(featureType)}
  <wfs:Delete typeName="${featureType.prefix}:${featureType.typeName}">
    <ogc:Filter>
      <ogc:PropertyIsEqualTo>
        <ogc:PropertyName>id</ogc:PropertyName>
        <ogc:Literal>${id}</ogc:Literal>
      </ogc:PropertyIsEqualTo>
    </ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>`;
}

export function buildDeleteAllTransaction(uncheckedFeatureType: WfsFeatureType): string {
  const featureType = validateFeatureType(uncheckedFeatureType);
  return `${transactionStart(featureType)}
  <wfs:Delete typeName="${featureType.prefix}:${featureType.typeName}">
    <ogc:Filter>
      <ogc:PropertyIsEqualTo>
        <ogc:PropertyName>status</ogc:PropertyName>
        <ogc:Literal>proposed</ogc:Literal>
      </ogc:PropertyIsEqualTo>
    </ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>`;
}

function firstTagText(xml: string, localName: string): string | undefined {
  const expression = new RegExp(
    `<(?:[A-Za-z_][\\w.-]*:)?${localName}[^>]*>([\\s\\S]*?)<\\/(?:[A-Za-z_][\\w.-]*:)?${localName}>`,
    "i"
  );
  const match = expression.exec(xml);
  return match ? decodeXml(match[1].replace(/<[^>]+>/g, "").trim()) : undefined;
}

function numericTag(xml: string, localName: string): number | undefined {
  const text = firstTagText(xml, localName);
  if (text === undefined || !/^\d+$/.test(text)) return undefined;
  return Number(text);
}

export function parseTransactionResponse(xml: string): WfsTransactionResult {
  const exception = firstTagText(xml, "ExceptionText")
    ?? firstTagText(xml, "ServiceException");
  if (exception || /<(?:[A-Za-z_][\w.-]*:)?(?:ExceptionReport|ServiceExceptionReport)\b/i.test(xml)) {
    return { ok: false, error: exception || "GeoServer rejected the transaction" };
  }

  const featureIds = [...xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?FeatureId\b[^>]*\bfid=["']([^"']+)["']/ig)]
    .map((match) => decodeXml(match[1]));
  const totalInserted = numericTag(xml, "totalInserted");
  const totalUpdated = numericTag(xml, "totalUpdated");
  const totalDeleted = numericTag(xml, "totalDeleted");
  const isSuccess = /<(?:[A-Za-z_][\w.-]*:)?SUCCESS\s*\/?\s*>/i.test(xml)
    || totalInserted !== undefined
    || totalUpdated !== undefined
    || totalDeleted !== undefined;

  if (!isSuccess) {
    return {
      ok: false,
      error: "GeoServer returned an unrecognized transaction response"
    };
  }

  return {
    ok: true,
    ...(featureIds.length ? { insertedFeatureId: featureIds[0], insertedFeatureIds: featureIds } : {}),
    ...(totalInserted !== undefined ? { totalInserted } : {}),
    ...(totalUpdated !== undefined ? { totalUpdated } : {}),
    ...(totalDeleted !== undefined ? { totalDeleted } : {})
  };
}
