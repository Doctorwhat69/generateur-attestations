import streamlit as st
import pdfplumber
import re
import io
import requests
from datetime import datetime
from docxtpl import DocxTemplate

# --- 1. FONCTIONS D'EXTRACTION ET DE VÉRIFICATION ---
def extraire_donnees_pdf(fichier_pdf):
    texte_complet = ""
    with pdfplumber.open(fichier_pdf) as pdf:
        for page in pdf.pages:
            texte_complet += page.extract_text() + "\n"
            
    texte_plat = texte_complet.replace('\n', ' ')
    donnees = {}

    match_ref = re.search(r"Réf\.:\s*(FAPRO[\d\-]+)", texte_complet)
    if match_ref: donnees["référence_facture"] = match_ref.group(1).strip()

    match_date = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", texte_complet)
    if match_date: 
        donnees["date_facture"] = match_date.group(1).strip()
        donnees["date_engagement"] = match_date.group(1).strip()

    match_nom = re.search(r"(?:Monsieur|Madame)\s+([A-Z\s]+)", texte_complet)
    if match_nom:
        parts = match_nom.group(1).strip().split(" ", 1)
        donnees["prenom_signataire"] = parts[0] if len(parts) > 0 else ""
        donnees["nom_signataire"] = parts[1] if len(parts) > 1 else ""

    match_adresse = re.search(r"Adresse des travaux:\s*(.*?)\s+(\d{5})\s+([^\n]+)", texte_complet)
    if match_adresse:
        donnees["adresse_chantier"] = donnees["adresse_client"] = donnees["adresse_beneficiaire"] = match_adresse.group(1).strip()
        donnees["cp_chantier"] = donnees["cp_client"] = donnees["code_postale_beneficiaire"] = match_adresse.group(2)
        donnees["ville_chantier"] = donnees["ville_client"] = donnees["ville"] = match_adresse.group(3).strip()

    match_tel = re.search(r"N[°o]tel:\s*([^\n]+)", texte_complet)
    if match_tel and "NEANT" not in match_tel.group(1).upper():
        donnees["telephone_client"] = match_tel.group(1).strip()

    match_visite = re.search(r"Date de visite préalable:\s*([^\n]+)", texte_complet)
    if match_visite:
        donnees["date_visite_prealable"] = match_visite.group(1).strip()

    # Extraction Sous-traitant (Recherche du SIRET à 14 chiffres)
    match_siret = re.search(r"par\s+(.*?)\s+N[°o]?\s*Siret:\s*(\d{14})", texte_plat, re.IGNORECASE)
    if match_siret:
        donnees["raison_sociale"] = match_siret.group(1).strip()
        donnees["siret"] = match_siret.group(2)

    # Extraction technique BAR-EN-101 / 102
    match_r = re.search(r"R=([0-9\.]+)m²\.K/W", texte_plat)
    if match_r: donnees["resistance_thermique"] = match_r.group(1)

    match_epaisseur = re.search(r"EPAISSEUR:\s*(\d+)mm", texte_plat)
    if match_epaisseur: donnees["epaisseur"] = match_epaisseur.group(1)

    # Extraction technique BAR-TH-171
    match_etas = re.search(r"Efficacité énergétique saisonnière à 35°C:\s*(\d+)%", texte_plat)
    if match_etas: donnees["ETAS"] = match_etas.group(1)

    donnees["date_du_jour"] = datetime.now().strftime("%d/%m/%Y")
    return donnees

def verifier_rge_ademe(siret):
    """Interroge l'API publique de l'ADEME pour vérifier le statut RGE."""
    siret_propre = str(siret).replace(" ", "")
    if len(siret_propre) != 14:
        return False, "Le SIRET doit contenir exactement 14 chiffres."
    
    url = f"https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2/lines?q={siret_propre}"
    
    try:
        reponse = requests.get(url, timeout=5) # Timeout court pour éviter que l'appli freeze
        if reponse.status_code == 200:
            data = reponse.json()
            if data.get('total', 0) > 0:
                domaines = list(set([ligne.get('domaine', 'Non précisé') for ligne in data['results']]))
                return True, f"✅ Artisan RGE valide ! Domaines couverts : {', '.join(domaines)}"
            else:
                return False, "❌ Aucun certificat RGE actif trouvé pour ce SIRET sur l'annuaire ADEME."
        else:
            return False, f"⚠️ L'API ADEME a retourné une erreur (Code {reponse.status_code})."
    except requests.exceptions.RequestException as e:
        return False, "⚠️ Impossible de joindre l'API. Votre réseau d'entreprise bloque peut-être la requête."

# --- 2. INTERFACE STREAMLIT ---
st.set_page_config(page_title="Générateur CEE", layout="wide")
st.title("📄 Générateur d'Attestations CEE")

# ÉTAPE 1 : Sélection des gestes
st.header("1. Quels gestes ont été réalisés ?")
col1, col2 = st.columns(2)
with col1:
    geste_101 = st.checkbox("BAR-EN-101 (Combles/Toiture)")
    geste_102 = st.checkbox("BAR-EN-102 (Murs extérieurs)")
with col2:
    geste_171 = st.checkbox("BAR-TH-171 (Pompe à chaleur)")
    geste_112 = st.checkbox("BAR-TH-112 (Chauffage au bois)")

gestes_selectionnes = any([geste_101, geste_102, geste_171, geste_112])

if not gestes_selectionnes:
    st.warning("Veuillez sélectionner au moins un geste ci-dessus pour continuer.")
else:
    # ÉTAPE 2 : Upload du PDF
    st.header("2. Importez la facture Proforma")
    fichier_pdf = st.file_uploader("Glissez le document PDF ici", type=["pdf"])

    if fichier_pdf:
        donnees_extraites = extraire_donnees_pdf(fichier_pdf)

        # ÉTAPE 3 : Vérification et Formulaire
        st.header("3. Vérification des données")
        
        # --- BLOC DE VÉRIFICATION RGE ---
        st.subheader("Contrôle Artisan")
        col_siret, col_btn = st.columns([2, 1])
        with col_siret:
            siret_a_verifier = st.text_input("N° SIRET extrait :", value=donnees_extraites.get("siret", ""))
        with col_btn:
            st.write("") # Espace pour aligner le bouton
            st.write("")
            if st.button("🔍 Vérifier le statut RGE"):
                if siret_a_verifier:
                    with st.spinner('Vérification sur les serveurs de l\'ADEME...'):
                        succes, message = verifier_rge_ademe(siret_a_verifier)
                        if succes:
                            st.success(message)
                        else:
                            st.error(message)
                else:
                    st.warning("Veuillez renseigner un SIRET.")
        
        st.markdown("---")

        with st.form("formulaire_donnees"):
            c1, c2 = st.columns(2)
            donnees_finales = {}
            
            with c1:
                st.subheader("Bénéficiaire")
                donnees_finales["nom_signataire"] = st.text_input("Nom", value=donnees_extraites.get("nom_signataire", ""))
                donnees_finales["prenom_signataire"] = st.text_input("Prénom", value=donnees_extraites.get("prenom_signataire", ""))
                donnees_finales["adresse_client"] = st.text_input("Adresse", value=donnees_extraites.get("adresse_client", ""))
                donnees_finales["cp_client"] = st.text_input("Code Postal", value=donnees_extraites.get("cp_client", ""))
                donnees_finales["ville_client"] = st.text_input("Ville", value=donnees_extraites.get("ville_client", ""))
                
            with c2:
                st.subheader("Informations Techniques & Pro")
                donnees_finales["date_visite_prealable"] = st.text_input("Date visite préalable", value=donnees_extraites.get("date_visite_prealable", ""))
                donnees_finales["référence_facture"] = st.text_input("Réf. Facture", value=donnees_extraites.get("référence_facture", ""))
                donnees_finales["raison_sociale"] = st.text_input("Sous-traitant (Raison Sociale)", value=donnees_extraites.get("raison_sociale", ""))
                donnees_finales["siret"] = st.text_input("Sous-traitant (SIRET)", value=siret_a_verifier)
                
                if geste_101 or geste_102:
                    donnees_finales["resistance_thermique"] = st.text_input("Résistance Thermique (R)", value=donnees_extraites.get("resistance_thermique", ""))
                    donnees_finales["epaisseur"] = st.text_input("Épaisseur (mm)", value=donnees_extraites.get("epaisseur", ""))
                    donnees_finales["surface_isolant"] = st.text_input("Surface d'isolant posée (m²)", value="")
                
                if geste_171:
                    donnees_finales["ETAS"] = st.text_input("Efficacité énergétique (ETAS %)", value=donnees_extraites.get("ETAS", ""))

            # Répercussion des variables pour toutes les balises
            donnees_finales.update({
                "adresse_chantier": donnees_finales["adresse_client"],
                "adresse_beneficiaire": donnees_finales["adresse_client"],
                "cp_chantier": donnees_finales["cp_client"],
                "code_postale_beneficiaire": donnees_finales["cp_client"],
                "ville_chantier": donnees_finales["ville_client"],
                "ville": donnees_finales["ville_client"],
                "raisonsociale_sous_traitant_iso": donnees_finales["raison_sociale"],
                "siret_sous_traitant_iso": donnees_finales["siret"],
                "date_du_jour": donnees_extraites["date_du_jour"]
            })

            bouton_generer = st.form_submit_button("Générer les attestations", type="primary")

        # ÉTAPE 4 : Génération
        if bouton_generer:
            st.header("4. Téléchargement")
            templates = {
                "101": ("BAR-EN-101 (v. A64.6) engageģes apreĢs le 01_04_2026 .docx", geste_101),
                "102": ("BAR-EN-102 (v. A65.4) engageģes apreĢs le 01_04_2026 .docx", geste_102),
                "171": ("BAR-TH-171 (v. A78.4) engageģes apreĢs le 01_04_2026 .docx", geste_171),
                "112": ("BAR-TH-112 (v. A46.3) engageģes apreĢs le 01_04_2026 .docx", geste_112)
            }

            for ref, (nom_fichier, est_selectionne) in templates.items():
                if est_selectionne:
                    try:
                        doc = DocxTemplate(nom_fichier)
                        doc.render(donnees_finales)
                        
                        buffer = io.BytesIO()
                        doc.save(buffer)
                        buffer.seek(0)
                        
                        nom_sortie = f"Attestation_BAR_{ref}_{donnees_finales['nom_signataire']}.docx"
                        st.download_button(
                            label=f"⬇️ Télécharger {nom_sortie}",
                            data=buffer,
                            file_name=nom_sortie,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                    except Exception as e:
                        st.error(f"Erreur avec le document {ref} : vérifiez que le template est bien dans le dossier.")