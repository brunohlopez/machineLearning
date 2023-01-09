import requests
import json
import overpy
import pandas as pd
import geopandas as gpd
from bs4 import BeautifulSoup
from itertools import chain
import osm2geojson
from shapely.geometry import Polygon, Point


class OSMGeometries:

    def __init__(self, overpass_query):
        self.overpass_query = overpass_query
        

    def nodes_dataframe(self):
        #get a list of tuple for the coordinates in WGS 84  
        
        lon_node = []
        lat_node = []
        id_node = []

        for node in self.overpass_query.nodes:
            lon_node.append(float(node.lon))
            lat_node.append(float(node.lat))
            id_node.append(int(node.id))

        final_frame = pd.DataFrame({"lon": lon_node, "lat": lat_node, "node_id": id_node})

        return final_frame

    
    def ways_dataframe(self):

        lon_node = []
        lat_node = []
        id_node = []
        id_way = []

        #First for loop over the ways
        for way in self.overpass_query.ways:
            #create a list of all of the different ways 
            the_nodes = way.get_nodes(resolve_missing = True)
            way_id = way.id
            #second for loop loops over the nodes
            for node in the_nodes:
                lon_node.append(float(node.lon))
                lat_node.append(float(node.lat))
                id_node.append(int(node.id))
                id_way.append(way_id)

        final_frame = pd.DataFrame({"lon": lon_node, "lat": lat_node, "node_id": id_node, 'way_id': id_way})

        return final_frame

    def relation_dataframe(self):

        lon_node = []
        lat_node = []
        id_node = []
        id_way = []
        id_relation = []

        for relation in self.overpass_query.relations:
            
            relation_id = relation.id
            the_relation = relation.members
            for way in the_relation:

                the_way = way.resolve(resolve_missing = True)
                way_id = the_way.id
                for node in the_way:
                    #get the nodes and append. We can do the final constructing of polygons in a seperate class or function
                    the_node = node.get_nodes(resolve_missing = True)
                    lon_mode = lon_node.append(float(the_node.way))
                    lat_node.append(float(the_node.lat))
                    id_node.append(int(the_node.id))
                    id_way.append(way_id)
                    id_relation(the_relation)

        final_frame = pd.DataFrame({"lon": lon_node, "lat": lat_node, "node_id": id_node, 'way_id': id_way, "relation_id" : id_relation})

        return final_frame

    def create_ways_geometry(self, geometries_csv, crs = 'EPSG:4326'):

        geometries_list = []

        for i in geometries_csv['way_id'].unique():

            filter_frame = geometries_csv[geometries_csv['way_id'] == i]

            if len(filter_frame > 1):
                lat_point_list = filter_frame['lat']
                lon_point_list = filter_frame['lon']

                polygon_geo = Polygon(zip(lon_point_list, lat_point_list))
                geometries_list.append(polygon_geo)

            elif len == 1:
                lat_point_list = filter_frame['lat']
                lon_point_list = filter_frame['lon']
                point_geo = Point(lon_point_list, lat_point_list)
                geometries_list.append(point_geo)

            else:
                continue

        return gpd.GeoDataFrame(geometry = geometries_list, crs = crs)
        


    def export_geom_shp(self, geometries_list, output_path, file_name):
        pass





def query_osm_data(overpass_query):

    api = overpy.Overpass()
    overpass_query = api.query(overpass_query)


def overpass_query_constructor_bbox(min_lat, min_lon, max_lat, max_lon, key = 'landuse', value = 'vineyard', timeout = 25):

    '''
    Constructs a query using overpy. First a bounding box using a min_lat min_lon, max_lat and max long. and then you
    can select the catefory

        Parameters:
            min_lat (float): Minimum Latitude (WGS-84)
            min_lon (float): Minimum Longitude (WGS-84)
            max_lat (float): Maximum Latitude (WGS-84)
            max_lon (float): Maximum Longitude (WGS-84)
            key (str): Key for OSM category (ex: https://wiki.openstreetmap.org/wiki/Key:landuse)
            value (str): Value for OSM category
            timeout (int): Timeout for query (seconds), if your query is timeout might be a lot of data.


        Returns:
            query_creator (str): Query to run using the overpy api


    '''

    #constructs a query based on a key, value and you can set timeout also
    query_creator = """
    [bbox: {0}, {1}, {2}, {3}]
    [timeout:{4}]
    ;
    (
        node[{5} = {6}];
        way[{5} = {6}];
        relation[{5} = {6}];
    );
    out center;

    """.format(str(min_lat), str(min_lon), str(max_lat), str(max_lon), str(timeout), key, value)

    return query_creator


def view_values(key, base_url = "http://wiki.openstreetmap.org/wiki/Key:"):
    '''
    Scrape OSM data given a specific key. Returns a table containg, the key, value, element, description, and rendering

        Parameters:
            key (str): A key from OSM. Here is
            an example of the key landuse https://wiki.openstreetmap.org/wiki/Key:landuse


        Returns:
            final_table (str): Returns a table containing all of the different valuies, elements and descriptions.


    '''

    full_url = base_url + key
    #send a requests to the created URL
    response = requests.get(full_url)

    #instantiate our beautiful soup object
    soup = BeautifulSoup(response.text, 'html.parser')
    soup_response = soup.find('table', {'class':'wikitable'})

    #put into a table pandas table
    read_html = pd.read_html(str(soup_response))
    final_frame = pd.DataFrame(read_html[0])

    #filter so that the only key is the key that was passed in

    return final_frame


if __name__ == '_main__':
    """
    [bbox:44.453388800301774,-0.56304931640625,46.240651955001695,2.3345947265625]
    [timeout:25]
    ;
    (
    node["landuse"="vineyard"];
    way["landuse"="vineyard"];
    relation["landuse"="vineyard"];
    );
    out center;
    """

