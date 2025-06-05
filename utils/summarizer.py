import openai
import os
import re

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def summarize_transcript(transcript, langs):
    detected_langs = [l.strip() for l in langs.split(",")]

    if len(detected_langs) > 1:
        target_lang = "en"
    else:
        target_lang = detected_langs[0]

    localized_headers = {
        "en": {
            "Summary": "Summary",
            "Key Topics": "Key Topics",
            "Decisions Made": "Decisions Made",
            "Action Items": "Action Items",
            "Next Steps": "Next Steps",
            "instruction": "Please write the transcript and summary in English."
        },
        "pt": {
            "Summary": "Resumo",
            "Key Topics": "Tópicos Principais",
            "Decisions Made": "Decisões Tomadas",
            "Action Items": "Tarefas",
            "Next Steps": "Próximos Passos",
            "instruction": "Escreva tudo, incluindo o resumo, em português do Brasil. Não use inglês em nenhuma parte da resposta."
        },
        "es": {
            "Summary": "Resumen",
            "Key Topics": "Temas Clave",
            "Decisions Made": "Decisiones Tomadas",
            "Action Items": "Tareas",
            "Next Steps": "Próximos Pasos",
            "instruction": "Escriba todo, incluido el resumen, en español. No use inglés en ninguna parte de la respuesta."
        },
        "fr": {
            "Summary": "Résumé",
            "Key Topics": "Sujets clés",
            "Decisions Made": "Décisions prises",
            "Action Items": "Tâches à effectuer",
            "Next Steps": "Prochaines étapes",
            "instruction": "Veuillez tout écrire en français, y compris le résumé. N'utilisez pas l'anglais."
        },
        "de": {
            "Summary": "Zusammenfassung",
            "Key Topics": "Wichtige Themen",
            "Decisions Made": "Getroffene Entscheidungen",
            "Action Items": "Aufgaben",
            "Next Steps": "Nächste Schritte",
            "instruction": "Bitte schreiben Sie alles, einschließlich der Zusammenfassung, auf Deutsch. Verwenden Sie kein Englisch."
        }
    }

    headers = localized_headers.get(target_lang, localized_headers["en"])

    prompt = f"""
You are a meeting assistant. I will provide you with the full transcript of a meeting.

If the meeting is conducted in more than one language, translate the full transcript and the summary into English.
If the meeting is entirely in a single language, keep the transcript and the summary in that language.

{headers["instruction"]}

Clean the transcript (correct punctuation and grammar), but do not remove hesitations or false starts—preserve the speaker's tone and structure.

Then, write a structured summary with the following format:

**Transcript**

(The cleaned transcript goes here.)

**{headers["Summary"]}**
(A brief paragraph summarizing the general topic and focus of the meeting.)

**{headers["Key Topics"]}**
Topic 1: (short explanation)
Topic 2: (short explanation)

**{headers["Decisions Made"]}**
(if any were made)

**{headers["Action Items"]}**
Task description – Owner (if known)

**{headers["Next Steps"]}**
(What’s expected next, if applicable)

Here's the transcript:
{transcript}
""".strip()

    system_message = {
        "role": "system",
        "content": f"You are a helpful assistant. Respond entirely in {target_lang}."
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            system_message,
            {"role": "user", "content": prompt}
        ]
    )

    full_output = response.choices[0].message.content.strip()

    # 🧠 Regex to split between **Transcript** and the following section (e.g., **Summary**)
    summary_header = headers["Summary"]
    transcript_pattern = r"\*\*Transcript\*\*[\s\n]*"
    summary_pattern = rf"\*\*{re.escape(summary_header)}\*\*"

    transcript_match = re.search(transcript_pattern, full_output, re.IGNORECASE)
    summary_match = re.search(summary_pattern, full_output, re.IGNORECASE)

    if transcript_match and summary_match:
        transcript_start = transcript_match.end()
        summary_start = summary_match.start()
        cleaned_transcript = full_output[transcript_start:summary_start].strip()
        summary_section = full_output[summary_start:].strip()
    else:
        # fallback: consider everything as summary
        cleaned_transcript = ""
        summary_section = full_output

    return cleaned_transcript, summary_section
