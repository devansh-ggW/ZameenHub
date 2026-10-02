const ALLOWED_ORIGIN = "*";

function cors(extra = {}) {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    ...extra
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...cors(), "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
  });
}

const text = (v, max) => typeof v === "string" ? v.trim().slice(0, max) : "";
const now = () => new Date().toISOString();

function validatePayload(body) {
  const required = ["ownerId","seller","purpose","type","title","price","area","city","locality"];
  for (const key of required) if (!text(body[key], 200)) return "Missing required field: " + key;
  if (!["Buy","Rent"].includes(body.purpose)) return "Invalid listing purpose.";
  if (text(body.title, 200).length < 3) return "Listing title is too short.";
  if (text(body.city, 100).length < 2 || text(body.locality, 120).length < 2) return "Location is incomplete.";
  if (!Array.isArray(body.images)) body.images = [];
  if (body.images.length > 5) return "Maximum 5 photos per listing.";
  for (const image of body.images) {
    if (typeof image !== "string" || !image.startsWith("data:image/")) return "Invalid property image.";
    // D1 maximum string/BLOB/table-row size is 2 MB. Leave headroom for SQLite row overhead.
    if (image.length > 1_800_000) return "Each property photo must be smaller than about 1.8 MB after compression.";
  }
  return null;
}

function mapProperty(row, image) {
  return {
    id: row.id,
    ownerId: row.owner_id,
    seller: row.seller_name,
    purpose: row.purpose,
    type: row.property_type,
    title: row.title,
    price: row.price,
    area: row.area,
    city: row.city,
    locality: row.locality,
    address: row.address || "",
    beds: row.bedrooms || "Not applicable",
    baths: row.bathrooms || "Not specified",
    parking: row.parking || "Not specified",
    furnishing: row.furnishing || "Not specified",
    description: row.description || "",
    phone: row.phone || "",
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    image: image || ""
  };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors() });

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    try {
      if (path === "/api/health" && request.method === "GET") {
        const result = await env.DB.prepare("SELECT 1 AS ok").first();
        return json({ ok: result?.ok === 1, service: "zameenhub-api", database: true });
      }

      if (path === "/api/properties" && request.method === "GET") {
        const q = text(url.searchParams.get("q"), 80).toLowerCase();
        const purpose = text(url.searchParams.get("purpose"), 20);
        const type = text(url.searchParams.get("type"), 80);
        const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 60, 1), 100);

        let rows;
        if (q && purpose) {
          rows = await env.DB.prepare(`
            SELECT p.*, (
              SELECT image_data FROM property_images i
              WHERE i.property_id=p.id ORDER BY i.sort_order LIMIT 1
            ) AS first_image
            FROM properties p
            WHERE p.status='published' AND p.purpose=? AND
              lower(p.title||' '||p.city||' '||p.locality||' '||p.property_type) LIKE ?
            ORDER BY p.created_at DESC LIMIT ?
          `).bind(purpose, "%" + q + "%", limit).all();
        } else if (purpose) {
          rows = await env.DB.prepare(`
            SELECT p.*, (
              SELECT image_data FROM property_images i
              WHERE i.property_id=p.id ORDER BY i.sort_order LIMIT 1
            ) AS first_image
            FROM properties p
            WHERE p.status='published' AND p.purpose=?
            ORDER BY p.created_at DESC LIMIT ?
          `).bind(purpose, limit).all();
        } else if (type) {
          rows = await env.DB.prepare(`
            SELECT p.*, (
              SELECT image_data FROM property_images i
              WHERE i.property_id=p.id ORDER BY i.sort_order LIMIT 1
            ) AS first_image
            FROM properties p
            WHERE p.status='published' AND p.property_type=?
            ORDER BY p.created_at DESC LIMIT ?
          `).bind(type, limit).all();
        } else {
          rows = await env.DB.prepare(`
            SELECT p.*, (
              SELECT image_data FROM property_images i
              WHERE i.property_id=p.id ORDER BY i.sort_order LIMIT 1
            ) AS first_image
            FROM properties p
            WHERE p.status='published'
            ORDER BY p.created_at DESC LIMIT ?
          `).bind(limit).all();
        }

        return json({ ok: true, properties: (rows.results || []).map(r => mapProperty(r, r.first_image)) });
      }

      const match = path.match(/^\/api\/properties\/([^/]+)$/);
      if (match && request.method === "GET") {
        const id = decodeURIComponent(match[1]);
        const row = await env.DB.prepare("SELECT * FROM properties WHERE id=? AND status='published'").bind(id).first();
        if (!row) return json({ ok: false, error: "Property not found." }, 404);
        const images = await env.DB.prepare(
          "SELECT image_data, sort_order FROM property_images WHERE property_id=? ORDER BY sort_order"
        ).bind(id).all();
        return json({
          ok: true,
          property: {
            ...mapProperty(row),
            images: (images.results || []).map(x => x.image_data)
          }
        });
      }

      if (path === "/api/properties" && request.method === "POST") {
        let body;
        try { body = await request.json(); }
        catch { return json({ ok: false, error: "Request body must be valid JSON." }, 400); }

        const error = validatePayload(body);
        if (error) return json({ ok: false, error }, 400);

        const id = crypto.randomUUID();
        const timestamp = now();
        const property = {
          id,
          ownerId: text(body.ownerId, 120),
          seller: text(body.seller, 60) || "ZameenHub User",
          purpose: text(body.purpose, 20),
          type: text(body.type, 80),
          title: text(body.title, 120),
          price: text(body.price, 40),
          area: text(body.area, 40),
          city: text(body.city, 80),
          locality: text(body.locality, 100),
          address: text(body.address, 160),
          beds: text(body.beds, 30),
          baths: text(body.baths, 30),
          parking: text(body.parking, 30),
          furnishing: text(body.furnishing, 40),
          description: text(body.description, 1600),
          phone: text(body.phone, 24)
        };

        const statements = [
          env.DB.prepare(`INSERT INTO properties
            (id,owner_id,seller_name,purpose,property_type,title,price,area,city,locality,address,bedrooms,bathrooms,parking,furnishing,description,phone,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'published',?,?)`)
            .bind(property.id,property.ownerId,property.seller,property.purpose,property.type,property.title,property.price,property.area,property.city,property.locality,property.address,property.beds,property.baths,property.parking,property.furnishing,property.description,property.phone,timestamp,timestamp)
        ];

        body.images.forEach((image, index) => {
          statements.push(
            env.DB.prepare(`INSERT INTO property_images (id,property_id,image_data,sort_order,created_at) VALUES (?,?,?,?,?)`)
              .bind(crypto.randomUUID(), id, image, index, timestamp)
          );
        });

        await env.DB.batch(statements);
        return json({ ok: true, property: { ...property, id, status: "published", createdAt: timestamp, updatedAt: timestamp } }, 201);
      }

      return json({ ok: false, error: "API endpoint not found." }, 404);
    } catch (error) {
      console.error(error);
      return json({ ok: false, error: "Server error." }, 500);
    }
  }
};
