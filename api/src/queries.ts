import * as Knex from "knex";
import * as _ from "lodash";
import * as nconf from "nconf";

interface BoundingBox {
  page: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

interface Citation {
  bounding_boxes: BoundingBox[];
  papers: string[];
}

type CitationsById = { [id: string]: Citation };

export class Connection {
  constructor(config: nconf.Provider) {
    this._knex = Knex({
      client: "pg",
      connection: {
        host: config.get("database:host"),
        port: config.get("database:port"),
        user: config.get("database:user"),
        password: config.get("database:password"),
        database: config.get("database:database")
      }
    });
  }

  async getCitationsForS2Id(s2Id: string) {
    return await this.getCitationsForPaper({ s2_id: s2Id });
  }

  async getCitationsForArxivId(arxivId: string) {
    return await this.getCitationsForPaper({ arxiv_id: arxivId });
  }

  async getCitationsForPaper(paperSelector: PaperSelector) {
    const rows = await this._knex("paper")
      .select(
        "citation.id as citation_id",
        "citationpaper.paper_id as cited_paper_id",
        "page",
        "left",
        "top",
        "width",
        "height"
      )
      .where(paperSelector)
      // Get citations.
      .join("citation", { "paper.s2_id": "citation.paper_id" })
      // Get bounding box for each citation.
      .join("entity", { "citation.id": "entity.entity_id" })
      .where({ "entity.type": "citation" })
      .join("entityboundingbox", { "entity.id": "entityboundingbox.entity_id" })
      .join("boundingbox", { "entityboundingbox.bounding_box_id": "boundingbox.id" })
      // Get S2 paper ID for each citation.
      .join("citationpaper", { "citation.id": "citationpaper.citation_id" });

    const citations: CitationsById = {};
    for (const row of rows) {
      const key = row["citation_id"];
      if (!citations.hasOwnProperty(key)) {
        citations[key] = {
          bounding_boxes: [],
          papers: []
        };
      }
      const s2Id = row["cited_paper_id"];
      const bounding_box: BoundingBox = {
        page: row.page,
        left: row.left,
        top: row.top,
        width: row.width,
        height: row.height
      };
      add_paper(citations[key], s2Id);
      add_bounding_box(citations[key], bounding_box);
    }
    return Object.values(citations);
  }

  private _knex: Knex;
}

function add_paper(citation: Citation, s2Id: string) {
  if (citation.papers.indexOf(s2Id) === -1) {
    citation.papers.push(s2Id);
  }
}

function add_bounding_box(citation: Citation, box: BoundingBox) {
  if (!citation.bounding_boxes.some(b => _.isEqual(b, box))) {
    citation.bounding_boxes.push(box);
  }
}

/**
 * Expected knex.js parameters for selecting a paper. Map from paper table column ID to value.
 */
type PaperSelector = ArxivIdPaperSelector | S2IdPaperSelector;

interface ArxivIdPaperSelector {
  arxiv_id: string;
}

interface S2IdPaperSelector {
  s2_id: string;
}
