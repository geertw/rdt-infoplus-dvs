"""
Module om InfoPlus_DVS objecten naar JSON te vertalen, ten behoeve
van de DVS HTTP interface.
"""

from datetime import timedelta
import urllib2
import socket
import json
import logging

_logger = logging.getLogger(__name__)

def trein_to_dict(trein, taal, tijd_nu, materieel=False, stopstations=False, serviceinfo_config=None):
    """
    Vertaal een InfoPlus_DVS Trein object naar een dict,
    geschikt voor een JSON output.
    Met de parameter materieel wordt de materieelcompositie teruggegeven,
    met de parameter stopstations alle stops per treinvleugel.
    """

    trein_dict = {}

    # Basis treininformatie
    trein_dict['treinNr'] = trein.treinnr
    trein_dict['vertrek'] = trein.lokaal_vertrek().isoformat()

    # Parse eindbestemming. Indien eindbestemming uit twee delen bestaat
    # (vleugeltrein), check dan of beide eindbestemmingen verschillen:
    if len(trein.eindbestemming_actueel) == 1:
        trein_dict['bestemming'] = trein.eindbestemming_actueel[0].lange_naam
    else:
        if trein.eindbestemming_actueel[0].lange_naam == trein.eindbestemming_actueel[1].lange_naam:
            # Eindbestemmingen gelijk
            trein_dict['bestemming'] = trein.eindbestemming_actueel[0].lange_naam
        else:
            # Verschillende eindbestemmingen:
            trein_dict['bestemming'] = '/'.join(bestemming.lange_naam
                for bestemming in trein.eindbestemming_actueel)

    trein_dict['soort'] = trein.soort
    trein_dict['soortAfk'] = trein.soort_code
    trein_dict['vertraging'] = float(round(float(trein.vertraging) / 60))
    trein_dict['spoor'] = '/'.join(str(spoor)
        for spoor in trein.vertrekspoor_actueel)

    if '/'.join(str(spoor) for spoor in trein.vertrekspoor) \
        != '/'.join(str(spoor) for spoor in trein.vertrekspoor_actueel):
        trein_dict['sprWijziging'] = True
    else:
        trein_dict['sprWijziging'] = False

    trein_dict['opmerkingen'] = trein.wijzigingen_str(taal, True, trein)
    trein_dict['tips'] = trein.tips(taal)

    if trein.statisch == True:
        if taal == 'en':
            trein_dict['opmerkingen'].append("No real-time information")
        else:
            trein_dict['opmerkingen'].append("Geen actuele informatie")

    # Voeg de treinnaam toe aan reistips:
    if trein.treinnaam != None:
        trein_dict['tips'].append(trein.treinnaam_str(taal))

    trein_dict['opgeheven'] = False
    trein_dict['status'] = trein.status
    trein_dict['vervoerder'] = trein.vervoerder

    # Trein opgeheven: wis spoor, vertraging etc.
    if trein.is_opgeheven():
        trein_dict['opgeheven'] = True
        trein_dict['spoor'] = None
        trein_dict['vertraging'] = 0

        # Toon geplande eindbestemming bij opgeheven trein:
        trein_dict['bestemming'] = '/'.join(bestemming.lange_naam
            for bestemming in trein.eindbestemming)

        # Controleer of vertrektijd meer dan 2 min geleden is:
        if trein.vertrek + timedelta(minutes=2) < tijd_nu:
            # Sla deze trein over. We laten opgeheven treinen tot 2 min
            # na vertrek in de feed zitten; vertrektijd van deze trein
            # is meer dan 2 minuten na vertrektijd
            return None

    else:
        # Trein is niet opgeheven

        # Stuur bij een gewijzigde eindbestemming
        # ook de oorspronkelijke eindbestemming mee:
        if trein_dict['bestemming'] != \
            '/'.join(bestemming.lange_naam \
        for bestemming in trein.eindbestemming):
            trein_dict['bestemmingOrigineel'] = '/'. \
                join(bestemming.lange_naam \
                for bestemming in trein.eindbestemming)

    # Verkorte (via)-route
    if trein_dict['opgeheven'] == True:
        verkorte_route = trein.verkorte_route
    else:
        verkorte_route = trein.verkorte_route_actueel

    if verkorte_route == None or len(verkorte_route) == 0:
        trein_dict['via'] = None
    else:
        trein_dict['via'] = ', '.join(
            via.middel_naam for via in verkorte_route)

    # Treinvleugels:
    trein_dict['vleugels'] = []
    for vleugel in trein.vleugels:
        vleugel_dict = {
            'bestemming': vleugel.eindbestemming_actueel.lange_naam}

        if materieel == True:
            vleugel_dict['mat'] = [
                (mat.treintype(), mat.eindbestemming_actueel.middel_naam)
                for mat in vleugel.materieel]

        if stopstations == True:
            vleugel_dict['stopstations'] = stopstations_to_list(
                vleugel.stopstations_actueel, trein.treinnr, 
                trein.rit_datum, serviceinfo_config)

        trein_dict['vleugels'].append(vleugel_dict)

    return trein_dict

def stopstations_to_list(stations, treinnr, ritdatum, serviceinfo_config):
    """
    Vertaal de stopstations van een trein naar een list van
    stopstations, geschikt om als JSON result terug te geven.
    """

    stations_list = []
    serviceinfo = retrieve_serviceinfo(treinnr, ritdatum, serviceinfo_config)

    for station in stations:
        station_dict = {'code': station.code, 'naam': station.lange_naam}
        
        extra_stop_data = None
        if serviceinfo != None:
            # Zoek halte op in serviceinfo.
            # TODO: beter omgaan met vleugeltreinen
            for service in serviceinfo:
                for stop in service['stops']:
                    if stop['station'].lower() == station.code.lower():
                        extra_stop_data = stop
                        break

            if extra_stop_data != None:
                # Verwerk spoorinformatie:
                station_dict['sprWijziging'] = False
                station_dict['aankomstspoor'] = extra_stop_data['scheduled_arrival_platform']
                station_dict['vertrekspoor'] = extra_stop_data['scheduled_departure_platform']

                if extra_stop_data['actual_arrival_platform'] != None and \
                    extra_stop_data['scheduled_arrival_platform'] != extra_stop_data['actual_arrival_platform']:
                    station_dict['aankomstspoor'] = extra_stop_data['actual_arrival_platform']
                    station_dict['sprWijziging'] = True

                if extra_stop_data['actual_departure_platform'] != None and \
                    extra_stop_data['scheduled_departure_platform'] != extra_stop_data['actual_departure_platform']:
                    station_dict['vertrekspoor'] = extra_stop_data['actual_departure_platform']
                    station_dict['sprWijziging'] = True

                # Overige data:
                station_dict['aankomst'] = extra_stop_data['arrival_time']
                station_dict['vertrek'] = extra_stop_data['departure_time']
                station_dict['vertragingAankomst'] = extra_stop_data['arrival_delay']
                station_dict['vertragingVertrek'] = extra_stop_data['departure_delay']

        # Voeg station toe aan de list met alle stations
        stations_list.append(station_dict)

    return stations_list


def retrieve_serviceinfo(treinnr, ritdatum, serviceinfo_config):
    """
    Haal extra serviceinformatie op om de informatie over stops te verrijken
    met bijvoorbeeld aankomsttijd etc.

    Bij fouten, uitgeschakelde serviceinfo configuratie, etc. geeft deze method None.
    In alle andere gevallen wordt de services dict van de rdt-serviceinfo API teruggegeven.
    """

    if serviceinfo_config == None or serviceinfo_config['enabled'] == False:
        return None
    else:
        try:
            trein_url = "%sservice/%s/%s" % (serviceinfo_config['url'], ritdatum, treinnr)
            response = urllib2.urlopen(trein_url, timeout=2)
            data = json.load(response)

            if 'services' in data:
                return data['services']
            else:
                return None
        except ValueError as error:
            _logger.error("Ongeldige JSON voor serviceinfo (datalengte: %s)")
        except urllib2.URLError as error:
            if not isinstance(error.reason, socket.timeout):
                _logger.warn("Serviceinfo timeout: %s", error)
            else:
                _logger.error("HTTP fout: %s. Geen serviceinfo beschikbaar", error)
        except Exception as error:
            _logger.error("Generieke fout: %s. Geen serviceinfo beschikbaar", error)
            return None
