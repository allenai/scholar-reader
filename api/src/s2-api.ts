import axios from "axios";
import { isS2ApiResponseSuccess, S2ApiPaper } from "./types/s2-api";

/**
 * Base URL for requests to Semantic Scholar API.
 */
const SEMANTIC_SCHOLAR_API_URL = "http://api.semanticscholar.org/v1";

interface Paper {
  s2Id: string;
  title: string;
  authors: string;
  abstract: string | null;
  url: string;
  venue: string | null;
  year: number | null;
}

export async function getPapers(s2Ids: string[]) {
  const results = await Promise.all(s2Ids.map(s2Id => getPaper(s2Id)));
  const papers = results.filter(paper => paper !== undefined);
  return papers;
}

async function getPaper(s2Id: string): Promise<Paper | undefined> {
  let response;
  try {
    response = await axios.get(`${SEMANTIC_SCHOLAR_API_URL}/paper/${s2Id}`);
  } catch (err) {
    console.error("Failed to fetch data from Semantic Scholar API", err);
    return;
  }
  if (isS2ApiResponseSuccess(response)) {
    const data = response.data as S2ApiPaper;
    const year = parseInt(data.year) || null;
    return {
      s2Id,
      title: data.title,
      authors: data.authors.map(a => a.name).join(", "),
      abstract: data.abstract,
      url: data.url,
      year,
      venue: data.venue
    };
  }
}
